from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    g,
    current_app,
    abort,
)
import json
import os
from authlib.integrations.flask_client import OAuth
from functools import wraps
from logger import setup_logging
from config import customer_file, product_file, email_template
from google.cloud import storage
from dotenv import load_dotenv
import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row, scalar_row
import atexit


# Initialize Logging
logger = setup_logging(__name__)

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

# DB_PARAMETERS = {
#     "host": os.environ.get("DB_HOST", "localhost"),
#     "dbname": os.environ.get("DB_NAME", "postgres"),
#     "user": os.environ.get("DB_USER", "postgres"),
#     "min_size": 1,
#     "max_size": 10,
# }

CONN_STR = os.environ.get("CONN_STR")
if not CONN_STR:
    logger.warniing(f"Connection String env variable not found")

# Initialize the DB connection pool
logger.info(f"Initializing the connection pool")

try:
    pool = ConnectionPool(conninfo=CONN_STR)
    logger.info(f"Connection pool initialized")

except Exception as e:
    logger.warning(f"Error initializing connection pool: {e}")


def get_db_pool():
    if pool is None:
        raise Exception("Database Pool not initialized")
    return pool


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


# Require API key for endpoints or just use the Session cookie for a logged in user.
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


def db_get_customers():
    pool = get_db_pool()

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
            SELECT c.id, c.name, c.email, ARRAY_AGG (JSON_BUILD_OBJECT('id', p.id, 'name', p.name) ORDER BY p.name) AS products
            FROM customers AS c
            LEFT JOIN customer_products AS cp on c.id = cp.customer_id
            LEFT JOIN products AS p on cp.product_id = p.id
            GROUP BY c.name, c.id, c.email
                """
            )
            customers = cur.fetchall()
            return customers


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


# Get the customer list
@app.route("/api/customers")
@require_api_key_or_session
# def get_customers():
#     """
#     Uses the load_customers function to open the Customer JSON file and return the data as a python object.

#     For the Dashboard- Displays the list of current customers on the customer page.


#     """
#     data = load_customers()
#     return data


def get_customers():
    customers = db_get_customers()
    return jsonify(customers)


# Add a new customer
@app.route("/api/customers", methods=["POST"])
@require_api_key_or_session
# def add_customer():
#     """
#     Post method. Takes a new customer in the Json format

#     Loops through the names of the current customers and checks that the new customer doesn't already exist.

#     Adds the new customer to the end of the customer list before saving the JSON customer file.
#     """
#     data = request.json

#     if not data.get("name") or not data.get("email"):
#         return jsonify({"error": "Name and email required"}), 400

#     customers = load_customers()

#     new_customer_name = data["name"]
#     new_customer_name_sanitized = data["name"].strip().lower()

#     for customer in customers:
#         if customer["name"].lower() == new_customer_name_sanitized:
#             return (
#                 jsonify({"error": f"Customer {new_customer_name} already exists"}),
#                 400,
#             )

#     new_customer = {
#         "name": data["name"].strip(),
#         "email": data["email"].strip(),
#         "products": data.get("products", []),
#     }

#     customers.append(new_customer)
#     save_customers(customers)

#     logger.info(f"New customer added: {new_customer}")


#     return jsonify(new_customer), 201
def add_customer():
    """
    Post method. Takes a new customer in the JSON format.

    Loops through the names of the current customers and checks that the new customer doesn't already exist.

    Adds the new customer to the database.

    """
    customer_request = request.json

    print(customer_request)

    if not customer_request.get("name") or not customer_request.get("email"):
        return jsonify({"error": "Name and email required"}), 400

    if db_customer_already_exists(customer_request["name"].strip().lower()):
        return (
            jsonify({"error": f"Customer {customer_request["name"]} already exists"}),
            400,
        )

    # customers = db_get_customers()

    # new_customer_name = customer_request["name"]
    # new_customer_name_sanitized = customer_request["name"].strip().lower()

    # for customer in customers:
    #     if customer["name"].lower() == new_customer_name_sanitized:
    #         return (
    #             jsonify({"error": f"Customer {new_customer_name} already exists"}),
    #             400,
    #         )

    new_customer = {
        "name": customer_request["name"].strip(),
        "email": customer_request["email"].strip(),
        "products": customer_request.get("products", []),
    }

    try:
        pool = get_db_pool()

        with pool.connection() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                    INSERT INTO customers (name, email, active)
                    VALUES (%s, %s, true) RETURNING id; """,
                        (new_customer["name"], new_customer["email"]),
                    )
                    new_customer_id = cur.fetchone()[0]
                    new_customer["id"] = new_customer_id

                    for product_id in new_customer["products"]:
                        cur.execute(
                            """
                        INSERT INTO customer_products (customer_id, product_id)
                        VALUES (%s, %s) """,
                            (new_customer["id"], product_id),
                        )

    except Exception as e:
        logger.error(f"Error adding customer to database: {e}")
        return jsonify({"error": "Error adding customer/products to database"}), 400

    logger.info(f"New customer added: {new_customer}")

    return jsonify(new_customer), 201


def db_customer_already_exists(new_customer_name):
    pool = get_db_pool()

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
            SELECT 1 from customers
            WHERE name=%s
            LIMIT 1
                """,
                (new_customer_name,),
            )
            return cur.fetchone() is not None


def db_get_products_per_customer(customer_id):
    pool = get_db_pool()

    with pool.connection() as conn:
        with conn.cursor(row_factory=scalar_row) as cur:
            cur.execute(
                """
                SELECT p.id 
                FROM products AS p
                JOIN customer_products AS cp on p.id = cp.product_id
                WHERE cp.customer_id = %s""",
                (customer_id,),
            )

            products = cur.fetchall()
            print(f"products: {products}")
            str_products = [str(product) for product in products]
            print(f"STRING PRODUCTS: {str_products}")
            return str_products


# Update a customer by index
@app.route("/api/customers/<customer_id>", methods=["PATCH"])
@require_api_key_or_session
# def update_customer(idx):
#     """
#     Put Method. Takes the index of the customer at the end of the url /<int:idx> and replaces the customer at that index with the payload in Json format:

#     """

#     data = request.json
#     customers = load_customers()

#     if idx < 0 or idx >= len(customers):
#         return jsonify({"error": "Customer not found"}), 404

#     if "name" in data:
#         updated_customer_name = data["name"]
#         updated_customer_name_sanitized = updated_customer_name.strip().lower()

#         for i, customer in enumerate(customers):
#             if i != idx and customer["name"].lower() == updated_customer_name_sanitized:

#                 return (
#                     jsonify(
#                         {"error": f"Customer {updated_customer_name} already exists"}
#                     ),
#                     400,
#                 )

#         data["name"] = data["name"].strip()

#     if "email" in data:
#         data["email"] = data["email"].strip()

#     customers[idx].update(data)
#     save_customers(customers)

#     logger.info(f"Updated customer: {data["name"]} at index {idx}")
#     return jsonify(customers[idx]), 201


def update_customer(customer_id):
    products_to_add = set()
    products_to_del = set()

    try:

        customer_update_request = request.json
        print(f"Customer Update Request: \n {customer_update_request}")
        customer = {"id": customer_id}

        if customer_update_request.get("name"):
            if db_customer_already_exists(customer_update_request["name"].strip()):
                return (
                    jsonify(
                        {
                            "error": f"Customer {customer_update_request["name"]} already exists"
                        }
                    ),
                    400,
                )
            customer["name"] = customer_update_request["name"]

        if customer_update_request.get("email"):
            customer["email"] = customer_update_request["email"]

        if "products" in customer_update_request:
            # customer["products"] = customer_update_request.get("products", [])

            new_ids = {
                product_id for product_id in customer_update_request.get("products", [])
            }

            current_ids = set(db_get_products_per_customer(customer_id))
            products_to_add = new_ids - current_ids
            products_to_del = current_ids - new_ids

        print(f"Customer after checks: {customer}")
        pool = get_db_pool()

        with pool.connection() as conn:
            with conn:
                with conn.cursor() as cur:

                    args = []
                    updates = []

                    if customer.get("name") or customer.get("email"):

                        if customer.get("name"):
                            args.append(customer["name"].strip())
                            updates.append(f"name = %s")

                        if customer.get("email"):
                            args.append(customer["email"].strip())
                            updates.append(f"email = %s")

                        if updates:
                            args.append(customer_id)

                            cur.execute(
                                f"UPDATE customers SET {", ".join(updates)} WHERE id=%s RETURNING name, email",
                                args,
                            )
                            updated_row = cur.fetchone()
                            customer["name"] = updated_row[0]
                            customer["email"] = updated_row[1]

                    if products_to_add:
                        for product_id in products_to_add:
                            cur.execute(
                                """
                            INSERT INTO customer_products (customer_id, product_id)
                            VALUES (%s, %s) """,
                                (customer_id, product_id),
                            )

                    if products_to_del:
                        cur.execute(
                            """
                        DELETE FROM customer_products
                        WHERE customer_id = %s 
                        AND product_id = ANY(%s)
                        """,
                            (customer_id, list(products_to_del)),
                        )

        return jsonify(customer), 201

    except Exception as e:
        logger.error(f"Error updating customer: {e}")
        return jsonify({"error:" "An unexpected errror occurred"}), 500


# Delete a customer by index
@app.route("/api/customers/<int:idx>", methods=["DELETE"])
@require_api_key_or_session
def delete_customer(idx):
    """
    Delete Method. Takes the index of the customer at the end of the url /<int:idx> and deletes that customer from the JSON file and saves it. Returns a 404 error if the customer is not found.

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


# Get the product list
@app.route("/api/products")
@require_api_key_or_session
# def get_products():
#     """
#     Opens the JSON product file and returns it as a python object

#     """
#     products = load_products()
#     return jsonify(products)


def db_get_products():
    pool = get_db_pool()

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
            SELECT id, name, price
            FROM products
            ORDER BY name      
            """
            )
            products = cur.fetchall()
            print(products)
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

    Saves it using the save_email_template function.

    Returns Success: True in JSON format
    """

    updated_email_template = request.json
    save_email_template(updated_email_template)

    logger.info(f"New email template: {updated_email_template}")
    return jsonify({"success": True})


@atexit.register
def close_db_pool():

    global pool
    if pool:
        print("Closing Global Connection Pool")
        pool.close()


if __name__ == "__main__":
    # Get port from environment (Cloud Run sets this)
    port = int(os.environ.get("PORT", 5000))

    # Only use insecure transport in local development
    if os.environ.get("FLASK_ENV") != "production":
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    app.run(host="127.0.0.1", port=port, debug=True)
