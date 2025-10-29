# UnderPin_Notification
Notifications for UnderPin Vending




Deployment to Google Cloud:

Create the docker repo for cloud-run-source :

gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker \
    --location=us-west2 \
    --description="Docker repository for Cloud Run images" \
    --project={project_name}


To build a new image from the updated code:

IMAGE_URL="us-west2-docker.pkg.dev/{project_name}/cloud-run-source-deploy/{project_name}-dashboard-image:latest"

gcloud builds submit --tag $IMAGE_URL

To Deploy:

gcloud run deploy {project_deployment_name} \
    --image $IMAGE_URL \
    --region us-west2 \
    --project {project_name}


To update the environmental variables- they can be updated in the Google Cloud Console. Go to Cloud Run -> Edit and deploy new revision. On the default containers tab click variables and secrets and change the parameters.

This is the command to download the configuration settings from the Google Cloud Console. 
gcloud run services describe {project_name} \
    --region us-west2 \
    --project {project_name} \
    --format export > service.yaml


If you want to apply YAML files to service:

gcloud run services replace service.yaml \
    --service {service_name} \
    --region us-west2 \
    --project {project_name}


To configure bucket:

gsutil cp customers.json gs://[YOUR-CONFIG-BUCKET-NAME]/customers.json
gsutil cp products.json gs://[YOUR-CONFIG-BUCKET-NAME]/products.json
gsutil cp email-template.json gs://[YOUR-CONFIG-BUCKET-NAME]/email-template.json

To run locally:

gcloud auth application-default login

Before running the app.py Flask dashboard.

FOR CRON JOB DEPLOYMENT

# Build the image and tag it for your Google Cloud registry. F flag sets the specific file (since it is not just Dockerignore)
docker build -f Dockerfile.cron -t gcr.io/{gcp-project_name}/{service_name}:latest .

# Authenticate Docker to GCR (if you haven't already)
gcloud auth configure-

# Push the Docker Image
docker push gcr.io/{gcp-project_name}/{service_name}:latest

# Build for amd64/linux architecture and push in one command
docker buildx build -f Dockerfile.cron --platform linux/amd64 -t gcr.io/{gcp-project_name}/{service_name}:latest --push .

# Deploy the service
gcloud run deploy {service_name} \
    --image gcr.io/{gcp-project_name}/{service_name} :latest \
    --platform managed \
    --region us-west2 \
    --no-allow-unauthenticated \
    --max-instances 1 \
    --port 8080 # Cloud Run requires a port, though this script won't use it

# Create the scheduler job
gcloud scheduler jobs create http {scheduler_job_name} \
    --schedule="0 8 * * *" \
    --uri="{service_url/}“ \
    --http-method=POST \
    --location=us-west2 \
    --oidc-service-account-email="underpin-sales-notification@underpin-notification.iam.gserviceaccount.com" \
    --oidc-token-audience="{service_url} no / at the end“
    --time-zone="America/Los_Angeles" 

# Immediately run to test

    gcloud scheduler jobs run {scheduler_jobname} --location=us-west2