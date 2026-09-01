# A dedicated service account for GitHub Actions to impersonate —
# never a human's own credentials, never a static key.
gcloud iam service-accounts create github-actions-deployer \
  --display-name="GitHub Actions CD deployer"

# Least privilege: exactly what deploying Cloud Run and applying
# Terraform requires, nothing broader — same principle as the
# container's own non-root user from Phase 2, Step 4.
gcloud projects add-iam-policy-binding ml-project-506908 \
  --member="serviceAccount:github-actions-deployer@ml-project-506908.iam.gserviceaccount.com" \
  --role="roles/run.admin"
gcloud projects add-iam-policy-binding ml-project-506908 \
  --member="serviceAccount:github-actions-deployer@ml-project-506908.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
gcloud projects add-iam-policy-binding ml-project-506908 \
  --member="serviceAccount:github-actions-deployer@ml-project-506908.iam.gserviceaccount.com" \
  --role="roles/storage.admin"   # scope this to just the state bucket via a bucket-level binding in a real hardening pass

# The Workload Identity Pool — the trust anchor GitHub's tokens get
# validated against.
gcloud iam workload-identity-pools create "github-pool" \
  --location="global" --display-name="GitHub Actions Pool"

# The OIDC provider inside it, trusting GitHub's token issuer —
# restricted to THIS repo specifically via attribute-condition. Without
# this line, any workflow from any GitHub repo that can obtain a
# GitHub OIDC token could potentially impersonate this service account.
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --location="global" --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='mohawwad93/YOLOsObjectDetectionAPI'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Allow ONLY workflows from that exact repo to impersonate the SA
gcloud iam service-accounts add-iam-policy-binding \
  "github-actions-deployer@ml-project-506908.iam.gserviceaccount.com" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/494443276988/locations/global/workloadIdentityPools/github-pool/attribute.repository/mohawwad93/YOLOsObjectDetectionAPI"

# The state bucket — bootstrap only, never managed by main.tf itself
gsutil mb -l us-central1 gs://yolos-objectdetection-api-tfstate
gsutil versioning set on gs://yolos-objectdetection-api-tfstate