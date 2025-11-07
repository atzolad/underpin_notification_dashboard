<h2 style="text-align:center;">Underpin Notification Dashboard</h2>


This is the Admin dashboard to update the settings for the [Underpin Notification Service](https://github.com/atzolad/underpin_notification_service)

The Dashboard is a FLASK APP backend which connects to a dashboard html template located in the templates directory. 

The dashboard is secured using Google Oauth 2.0 Users will need to login to their google accounts to access. Because of the nature of Google Oauth, an additional allowed_users parameter exists in the env file to specify which google users have access. 

It is designed so that a non-technical user can adjust the settings used by the notification service without requiring code. 

Can Add/Remove/Edit Customers and Products as well as edit the email template that the notifications are based on. 

**NOTE** Due to the current limitations of the system response- the notification system is designed to identify customers by the products that they own. The product names in this app must match those from the machine and the product names in the machine must be unique. 

Planned future functionality is to retrieve the list of products directly from the machine every day when this runs, to ensure that the product names match. 

At the moment the Customer/Product list are stored as JSON files in leiu of a true Database for MVP. With a small customer/product list this was quick to implement and still responsive. Future plans are to update this to a true database. Possibly sql.lite. These files are stored in a google bucket named {gcp_project_name-files} - replace gcp_project_name with the actual gcp project name. 


## Deployment to Google Cloud:

### Create the docker repo for cloud-run-source :
```
gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker \
    --location=us-west2 \
    --description="Docker repository for Cloud Run images" \
    --project={project_name}
```


### Build a new image from the updated code:
```
IMAGE_URL="us-west2-docker.pkg.dev/{project_name}/cloud-run-source-deploy/{project_name}-image:latest"
```
```
gcloud builds submit --tag $IMAGE_URL
```
### To Deploy:
```
gcloud run deploy {project_deployment_name} \
    --image $IMAGE_URL \
    --region us-west2 \
    --project {project_name}
```
-------------------------------------------------------------------------------------  

### Add the environmental variables within the Google Cloud project. 

On the Cloud Console go to Cloud Run -> click on the correct service. Click Edit and deploy new revision -> Variables and Secrets -> can add environmental variables there. Pasting the copied list from .env will populate them all.

With the current deployment I also saved the credentials.json file for the Google APIs in Secrets Manager. This requires mounting a volume to the service for it to be able to read the file. 

To download the configuration settings from the Google Cloud Console:

```
gcloud run services describe {project_name} \
    --region us-west2 \
    --project {project_name} \
    --format export > service.yaml
```

To apply YAML files to service:
```
gcloud run services replace service.yaml \
    --service {service_name} \
    --region us-west2 \
    --project {project_name}
```

To configure bucket:
```
gsutil cp customers.json gs://[YOUR-CONFIG-BUCKET-NAME]/customers.json
gsutil cp products.json gs://[YOUR-CONFIG-BUCKET-NAME]/products.json
gsutil cp email-template.json gs://[YOUR-CONFIG-BUCKET-NAME]/email-template.json
```


---------------------------------------------------------------------------

To run locally (before running the app.py Flask dashboard):
```
gcloud auth application-default login
```


Then run the Flask dashboard with 
```python
python3 app.py
``` 
and acesss at http://127.0.0.1:5000