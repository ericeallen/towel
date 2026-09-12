"""
Example 2: Repeated code in classes.

Tests: Same-file duplicate detection in class methods.
"""


def _extracted_func_0(__param_0, self):
    if not __param_0:
        raise ValueError('Email is required')
    if '@' not in __param_0:
        raise ValueError('Invalid email format')
    if len(__param_0) < 5:
        raise ValueError('Email too short')


class EmailProcessor:
    """Process emails."""

    def __init__(self, email):
        self.email = email

    def process(self):
        """Process email."""
        # Validation logic
        _extracted_func_0(self.email, self)

        print(f"Processing email: {self.email}")
        return True


class SMSProcessor:
    """Process SMS messages."""

    def __init__(self, phone):
        self.phone = phone

    def process(self):
        """Process SMS."""
        # Validation logic (DUPLICATE!)
        _extracted_func_0(self.phone, self)

        print(f"Processing SMS: {self.phone}")
        return True


class PushNotificationProcessor:
    """Process push notifications."""

    def __init__(self, device_id):
        self.device_id = device_id

    def process(self):
        """Process push notification."""
        # Validation logic (DUPLICATE!)
        _extracted_func_0(self.device_id, self)

        print(f"Processing push: {self.device_id}")
        return True
