"""
Example 2: Repeated code in classes.

Tests: Same-file duplicate detection in class methods.
"""


class EmailProcessor:
    """Process emails."""

    def __init__(self, email):
        self.email = email

    def process(self):
        """Process email."""
        # Validation logic
        if not self.email:
            raise ValueError("Email is required")
        if "@" not in self.email:
            raise ValueError("Invalid email format")
        if len(self.email) < 5:
            raise ValueError("Email too short")

        print(f"Processing email: {self.email}")
        return True


class SMSProcessor:
    """Process SMS messages."""

    def __init__(self, phone):
        self.phone = phone

    def process(self):
        """Process SMS."""
        # Validation logic (DUPLICATE!)
        if not self.phone:
            raise ValueError("Email is required")
        if "@" not in self.phone:
            raise ValueError("Invalid email format")
        if len(self.phone) < 5:
            raise ValueError("Email too short")

        print(f"Processing SMS: {self.phone}")
        return True


class PushNotificationProcessor:
    """Process push notifications."""

    def __init__(self, device_id):
        self.device_id = device_id

    def process(self):
        """Process push notification."""
        # Validation logic (DUPLICATE!)
        if not self.device_id:
            raise ValueError("Email is required")
        if "@" not in self.device_id:
            raise ValueError("Invalid email format")
        if len(self.device_id) < 5:
            raise ValueError("Email too short")

        print(f"Processing push: {self.device_id}")
        return True
