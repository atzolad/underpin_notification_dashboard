from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    g,
)
import json
import os
from authlib.integrations.flask_client import OAuth
from functools import wraps
from logger import setup_logging
from config import customer_file, product_file, email_template
from google.cloud import storage
from dotenv import load_dotenv

# Initialize Logging
logger = setup_logging(__name__, log_file="Dashboard_log")

# Load environmental variables
load_dotenv()

# Initialize Flask App Instance
app = Flask(__name__)

# Retrieve environmental variables
app.secret_key = os.environ.get("SECRET_KEY")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
API_KEY = os.environ.get("API_KEY")
ALLOWED_USERS = [
    email.strip().lower()
    for email in os.getenv("ALLOWED_USERS", "").split(",")
    if email.strip()
]

# Get base URL from environment
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")

# Use an environment variable to define the bucket name for Google Cloud Storage
BUCKET_NAME = os.environ.get("CONFIG_BUCKET")
# storage_client = storage.Client()


def get_storage_client():
    """
    Creates and stores the client bucket / storage client on the first call within a request

    Flask stores this information in "g"

    """
    # Check and see if a storage_client already exists within Flask

    if "storage_client" not in g:
        g.storage_client = storage.Client()
        g.storage_bucket = g.storage_client.bucket(BUCKET_NAME)

    return g.storage_bucket


@app.teardown_appcontext
def teardown_storage_client(exception=None):
    storage_client = g.pop("storage_client", None)


oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid profile email"},
)


# Create login for Google
@app.route("/login/google")
def google_login():
    try:

        redirect_uri = f"{BASE_URL}/google/auth/"
        print(redirect_uri)
        return google.authorize_redirect(redirect_uri, prompt="select_account")
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        return jsonify({"error:" f"Error during login: {str(e)}"}), 500


def is_user_allowed(email):
    return email.lower() in ALLOWED_USERS


# Authorize Google
@app.route("/google/auth/")
def google_auth():
    oauth.google.authorize_access_token()
    user = oauth.google.userinfo()
    user_email = user["email"]
    logger.info(f"Google User: {str(user)}")

    # Re-direct user to the unauthorized page if they are not on the authorized user list. They can return to the main page from here and login to a different account.
    if not is_user_allowed(user_email):
        return render_template("unauthorized.html", email=user_email), 403

    # Store the user info in the session for later api endpoint checks.

    session["user"] = {
        "id": user["sub"],
        "name": user["name"],
        "email": user["email"],
        "picture": user["picture"],
    }
    logged_in_user = user["name"]
    logger.info(f"Logged in user: {logged_in_user}")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("google_login"))


# Require login for dashboard
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("google_login"))
        return f(*args, **kwargs)

    return decorated_function


# Require API key- for endpoints or just use the Session cookie for a logged in user.
def require_api_key_or_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):

        # Check if user is logged in via session (from dashboard)
        if "user" in session:
            return f(*args, **kwargs)

        provided_key = request.headers.get("X-API-Key")
        if provided_key and provided_key == API_KEY:
            return f(*args, **kwargs)
        return jsonify({"error": "Invalid or missing API key"}), 401

    return decorated_function


# Render the main dashboard
@app.route("/")
@login_required
def dashboard():

    return render_template("dashboard.html", user=session["user"])


def load_customers():
    """
    Opens the JSON customer file from Google Cloud and returns the loaded JSON data

    """
    bucket = get_storage_client()
    blob = bucket.blob(customer_file)
    logger.info("Reading customers from: %s", BUCKET_NAME)

    try:
        # download_as_bytes() returns the content, which we decode to a string
        customer_string = blob.download_as_bytes().decode("utf-8")

        # 3. Load and return the JSON data
        data = json.loads(customer_string)
        return data

    except Exception as e:
        # Handle cases where the file doesn't exist or is empty
        logger.error(f"Error reading {customer_file} from GCS: {e}")
        return []  # Return empty list or handle the error


def save_customers(customers):
    """
    Takes the new customer list in JSON format as input. Opens the customer file in "write" mode and writes the new list to the file.

    See example in example_json/customers/json

    """

    bucket = get_storage_client()
    blob = bucket.blob(customer_file)

    try:
        # Encode the JSON data
        customer_string = json.dumps(customers, indent=2)

        blob.upload_from_string(customer_string, content_type="application/json")
        logger.info(f"Saved file {customer_file} to GCS bucket:")

    except Exception as e:
        logger.error(f"Error writing {customer_file} to GCS bucket: {e}")

    #   try:
    #     with open (customer_file, "w") as cf:
    #             json.dump(customers, cf,  indent=2)
    #   except Exception as e:
    #       logger.error(f"Error writing to customer file: {e}")


# Get the customer list
@app.route("/api/customers")
@require_api_key_or_session
def get_customers():
    """
    Uses the load_customers function to open the Customer JSON file and return the data as a python object.

    For the Dashboard- Displays the list of current customers on the customer page.

    """
    data = load_customers()
    return data


# Add a new customer
@app.route("/api/customers", methods=["POST"])
@require_api_key_or_session
def add_customer():
    """
    Post method. Takes a new customer in the Json format

    Loops through the names of the current customers and checks that the new customer doesn't already exist.

    Adds the new customer to the end of the customer list before saving the JSON customer file.
    """
    data = request.json

    if not data.get("name") or not data.get("email"):
        return jsonify({"error": "Name and email required"}), 400

    customers = load_customers()

    new_customer_name = data["name"]
    new_customer_name_sanitized = data["name"].strip().lower()

    for customer in customers:
        if customer["name"].lower() == new_customer_name_sanitized:
            return (
                jsonify({"error": f"Customer {new_customer_name} already exists"}),
                400,
            )

    new_customer = {
        "name": data["name"].strip(),
        "email": data["email"].strip(),
        "products": data.get("products", []),
    }

    customers.append(new_customer)
    save_customers(customers)

    logger.info(f"New customer added: {new_customer}")

    return jsonify(new_customer), 201


# Update a customer by index
@app.route("/api/customers/<int:idx>", methods=["PUT"])
@require_api_key_or_session
def update_customer(idx):
    """
    Put Method. Takes the index of the customer at the end of the url /<int:idx> and replaces the customer at that index with the payload in Json format:

    updated_customer = {
        "name": Newname,
        "email": Newemail,
        "products": New product 1, New product 2)
    }

    """

    data = request.json
    customers = load_customers()

    if idx < 0 or idx >= len(customers):
        return jsonify({"error": "Customer not found"}), 404

    if "name" in data:
        updated_customer_name = data["name"]
        updated_customer_name_sanitized = updated_customer_name.strip().lower()

        for i, customer in enumerate(customers):
            if i != idx and customer["name"].lower() == updated_customer_name_sanitized:

                return (
                    jsonify(
                        {"error": f"Customer {updated_customer_name} already exists"}
                    ),
                    400,
                )

        data["name"] = data["name"].strip()

    if "email" in data:
        data["email"] = data["email"].strip()

    customers[idx].update(data)
    save_customers(customers)

    logger.info(f"Updated customer: {data["name"]} at index {idx}")
    return jsonify(customers[idx]), 201


# Delete a customer by index
@app.route("/api/customers/<int:idx>", methods=["DELETE"])
@require_api_key_or_session
def delete_customer(idx):
    """
    Delete Method. Takes the index of the customer at the end of the url /<int:idx> and deletes that customer from the JSON file and saves it. Returns a 404 error if the customer is not found.

    updated_customer = {
        "name": Newname,
        "email": Newemail,
        "products": New product 1, New product 2)
    }

    """

    customers = load_customers()

    if idx < 0 or idx >= len(customers):
        return jsonify({"error": "Customer not found"}), 404

    customer_to_be_del = customers[idx]
    customers.pop(idx)
    save_customers(customers)

    logger.info(f"Deleted customer: {customer_to_be_del} at index: {idx}")
    return jsonify({"deleted": f"Customer {customer_to_be_del["name"]} deleted!"}), 200


# Load the product list
def load_products():
    """
    Opens the JSON product file and returns it as a python object

    """

    bucket = get_storage_client()
    blob = bucket.blob(product_file)
    logger.info("Reading products from: %s", BUCKET_NAME)

    try:
        # download_as_bytes() returns the content, which we decode to a string
        products_string = blob.download_as_bytes().decode("utf-8")

        # 3. Load and return the JSON data
        data = json.loads(products_string)
        return data

    except Exception as e:
        # Handle cases where the file doesn't exist or is empty
        logger.error(f"Error reading {product_file} from GCS: {e}")
        return []  # Return empty list or handle the error

    # if os.path.exists(product_file):
    #     try:

    #         with open (product_file, "r") as pf:
    #             data = json.load(pf)
    #             return data

    #     except Exception as e:
    #         logger.error(f"Error opening customer file: {e}")
    #         return f"Error opening customer file: {e}"

    # return "OS Path doesn't exist"


# Save the new product list
def save_products(products):
    """
    Takes a JSON formatted list as input.

    Saves new product list to a JSON file.
    """
    bucket = get_storage_client()
    blob = bucket.blob(product_file)
    logger.info(f"Reading products from: {BUCKET_NAME}")

    try:
        # Encode the JSON data
        products_string = json.dumps(products, indent=2)

        blob.upload_from_string(products_string, content_type="application/json")
        logger.info(f"Saved file {product_file} to GCS bucket:")

    except Exception as e:
        logger.error(f"Error writing {product_file} to GCS bucket: {e}")

    #   try:
    #     with open (product_file, "w") as pf:
    #             json.dump(products, pf,  indent=2)
    #   except Exception as e:
    #       logger.error(f"Error writing to customer file: {e}")


# Get the product list
@app.route("/api/products")
@require_api_key_or_session
def get_products():
    """
    Opens the JSON product file and returns it as a python object

    """
    products = load_products()
    return jsonify(products)


# Add a new product
@app.route("/api/products", methods=["POST"])
@require_api_key_or_session
def add_product():
    """
    POST method. Accepts a body of:


     {
    "name": "Newname",
    "price": "99.99")
    }

    If the product name does not already exist- it adds the new product to the product list JSON file and saves it. Returns a 400 error if the product already exists, otherwise returns the info for the new product in JSON format.

    """
    data = request.json
    products = load_products()
    new_product_name = data["name"]
    new_product_name_sanitized = new_product_name.strip().lower()

    for product in products:
        if product["name"].lower() == new_product_name_sanitized:
            return jsonify({"error": f"Product {new_product_name} already exists"}), 400

    new_product = {"name": data["name"].strip(), "price": float(data["price"])}

    products.append(new_product)
    save_products(products)

    logger.info(f"Added Product: {new_product["name"]}")
    return jsonify(new_product), 201


# Update a product by index
@app.route("/api/products/<int:idx>", methods=["PUT"])
@require_api_key_or_session
def update_product(idx):
    """
    PUT Method. Takes the index of the product at the end of the url /<int:idx> and replaces the product at that index with the payload in Json format. Returns a 404 error if the product isn't found.



    {
        "name": "NewProduct",
        "price": "99.99",
    }

    """
    data = request.json
    products = load_products()

    if idx < 0 or idx >= len(products):
        return jsonify({"error": "Product not found"}), 404

    if "name" in data:
        updated_product_name = data["name"]
        updated_product_name_sanitized = updated_product_name.strip().lower()

        for i, product in enumerate(products):
            if i != idx and product["name"].lower() == updated_product_name_sanitized:

                return (
                    jsonify(
                        {"error": f"Customer {updated_product_name} already exists"}
                    ),
                    400,
                )

    updated_product = {"name": data["name"].strip(), "price": float(data["price"])}

    products[idx].update(updated_product)
    save_products(products)

    logger.info(f"Update product: {products[idx]} at index: {idx}")
    return jsonify(products[idx])


# Delete a product by index
@app.route("/api/products/<int:idx>", methods=["DELETE"])
@require_api_key_or_session
def delete_product(idx):
    """
    DELETE method. Takes the index of the product at the end of the url /<int:idx> and deletes that product from the product list. Returns a 404 error if the product isn't found.

    Args: <int:idx>

    """

    products = load_products()

    if idx < 0 or idx > len(products):
        return jsonify({"error": "Product not found"}), 404

    product_to_be_del = products[idx]
    deleted_prod = products.pop(idx)
    save_products(products)

    logger.info(f"delete product: {deleted_prod} at index: {idx}")
    return jsonify({"deleted": f"Product {product_to_be_del["name"]} deleted!"}), 200


def load_email_template():
    """
    Opens the email template JSON file from Google Cloud bucket and returns it as a python object. For the dashboard effectively displays the current email template.

    The email template filename is hard coded in the config file.

    Returns a default template if the file is not found.

    """

    bucket = get_storage_client()
    blob = bucket.blob(email_template)
    logger.info("Reading email_template from: %s", BUCKET_NAME)

    try:
        # download_as_bytes() returns the content, which we decode to a string
        email_template_string = blob.download_as_bytes().decode("utf-8")

        # 3. Load and return the JSON data
        data = json.loads(email_template_string)
        return data

    except Exception as e:
        # Handle cases where the file doesn't exist or is empty
        logger.error(f"Error opening email template. Returning default")

        return {
            "subject": "{customer_name} Daily Sales Report for {date}",
            "greeting": "Dear {customer_name},",
            "header": "Here's your sales summary for {date}:\n\n",
            "sign_off": "Thank you,",
            "signature": "The Underpin Team",
            "total_revenue": "Your total revenue from yesterday's sales:",
        }


def save_email_template(updated_email_template):
    """
    Accepts the updated email template in JSON format:

    {
    "subject": "{customer_name} Daily Sales Report for {date}",
    "greeting": "Dear {customer_name},",
    "header": "Here's your sales summary for {date}:\n",
    "sign_off": "Thank you,",
    "signature": "The Underpin Team",
    "total_revenue": "Your total revenue from yesterday's sales:"
    }

    And saves it to the email_template file specified in the config.
    """

    bucket = get_storage_client()
    blob = bucket.blob(email_template)

    try:
        # Encode the JSON data
        updated_email_template_string = json.dumps(updated_email_template, indent=2)

        blob.upload_from_string(
            updated_email_template_string, content_type="application/json"
        )
        logger.info(f"Saved file {email_template} to GCS bucket:")

    except Exception as e:
        logger.error(f"Error writing {email_template} to GCS bucket: {e}")

    # try:
    #     with open (email_template, "w") as et:
    #             json.dump(updated_email_template, et,  indent=2)
    # except Exception as e:
    #     logger.error(f"Error writing to email_template: {e}")


# Get the email template
@app.route("/api/email-template")
@require_api_key_or_session
def get_email_template():
    """
    Loads the email template using the load_email_template function. Returns the data in JSON format.
    """
    data = load_email_template()
    return jsonify(data)


# Update the email template
@app.route("/api/email-template", methods=["POST"])
@require_api_key_or_session
def update_email_template():
    """
    POST method. Accepts the updated email template in JSON format:

    {
    "subject": "{customer_name} Daily Sales Report for {date}",
    "greeting": "Dear {customer_name},",
    "header": "Here's your sales summary for {date}:\n",
    "sign_off": "Thank you,",
    "signature": "The Underpin Team",
    "total_revenue": "Your total revenue from yesterday's sales:"
    }

    Saves it using the save_email_template function.

    Returns Success: True in JSON format

    """

    updated_email_template = request.json
    save_email_template(updated_email_template)

    logger.info(f"New email template: {updated_email_template}")
    return jsonify({"success": True})


if __name__ == "__main__":
    # Get port from environment (Cloud Run sets this)
    port = int(os.environ.get("PORT", 5000))

    # Only use insecure transport in local development
    if os.environ.get("FLASK_ENV") != "production":
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    app.run(host="127.0.0.1", port=port, debug=True)
