import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .ml.yolos_engine import YolosDetectionEngine

logger = logging.getLogger(__name__)

# Comfortably under Kubernetes' default terminationGracePeriodSeconds (30s) —
# leaves buffer for the drain itself plus process exit before SIGKILL lands.
SHUTDOWN_GRACE_PERIOD_SECONDS = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = YolosDetectionEngine(settings.model_name, settings.device_preference)

    # Published immediately, before any loading happens — app.state is
    # never half-populated, and /healthz + /readyz are both meaningful
    # from the first millisecond of the process's life.
    app.state.engine = engine
    app.state.active_sessions = set()  # open WebSockets — see graceful shutdown
    app.state.shutting_down = (
        False  # readyz consults this independently of engine.status
    )

    # Fire-and-forget: NOT awaited here. This is what lets the server
    # start accepting connections immediately below, instead of the
    # whole app being unreachable for the entire 10-30s load window.
    load_task = asyncio.create_task(_load_and_warm_engine(engine))

    yield  # <-- server now accepting connections; engine may still be loading

    # ---------------- Shutdown path (triggered by SIGTERM) ----------------
    logger.info("Shutdown: marking not-ready, no longer accepting new work")
    app.state.shutting_down = True  # readyz flips to 503 on the very next probe

    if not load_task.done():
        load_task.cancel()  # loading never finished — no point continuing it

    await _drain_active_sessions(app.state.active_sessions)
    logger.info("Shutdown complete")


async def _load_and_warm_engine(engine: YolosDetectionEngine) -> None:
    """
    Runs concurrently with the server accepting connections. Any
    exception here is caught and logged, not re-raised — there's no
    caller left to propagate to once this is a detached background task.
    engine.load()/warm_up() already set status=FAILED internally, which
    is what both /readyz (permanently) and /healthz (via the FAILED
    exception in §1) key off of.
    """
    try:
        engine.load()
        engine.warm_up()
        logger.info("Engine ready: status=%s", engine.status)
    except asyncio.CancelledError:
        raise  # shutdown mid-load — let cancellation propagate normally
    except Exception:
        logger.exception("Engine failed to initialize — readiness will never succeed")


async def _drain_active_sessions(sessions: set) -> None:
    """
    Gives every open WebSocket a bounded grace period to close cleanly,
    instead of the connection just dying when the process exits (which
    the client sees as an abrupt reset, not a clean disconnect).
    """
    if not sessions:
        return

    async def _close_one(websocket) -> None:
        with contextlib.suppress(Exception):
            await websocket.close(code=1001, reason="Server shutting down")

    pending = [asyncio.create_task(_close_one(ws)) for ws in list(sessions)]
    _, still_pending = await asyncio.wait(
        pending, timeout=SHUTDOWN_GRACE_PERIOD_SECONDS
    )
    for task in still_pending:
        task.cancel()  # grace period elapsed — force it, SIGKILL is coming soon regardless
