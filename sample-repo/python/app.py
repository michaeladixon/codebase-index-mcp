"""Entry point for the sample python app."""
import sys

from models import Order, create_user
from utils import Config, validate_email


def main() -> int:
    user = create_user("Ada Lovelace", "ada@example.com")
    order = Order(user=user)
    order.add_item({"sku": "widget", "price": 9.5})
    if not validate_email(user.email):
        return 1
    print(user.display_name(), order.total())
    user.deactivate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
