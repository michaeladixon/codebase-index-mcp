"""Domain models."""
from dataclasses import dataclass, field

from utils import slugify, validate_email


@dataclass
class User:
    """A registered user."""
    name: str
    email: str
    active: bool = True

    def deactivate(self) -> None:
        """Soft-delete this user."""
        self.active = False

    def display_name(self) -> str:
        return f"{self.name} <{self.email}>"


@dataclass
class Order:
    """A purchase order belonging to a user."""
    user: User
    items: list = field(default_factory=list)

    def total(self) -> float:
        return sum(i.get("price", 0) for i in self.items)

    def add_item(self, item: dict) -> None:
        self.items.append(item)


def create_user(name: str, email: str) -> User:
    """Validate input and build a User."""
    if not validate_email(email):
        raise ValueError(f"bad email: {email}")
    return User(name=slugify(name), email=email)
