"""
Example 2: Repeated code in classes.

Tests: Same-file duplicate detection in class methods.
"""


def _extracted_func_0(__param_0, __param_1):
    if not __param_0():
        raise ValueError("Email is required")
    if "@" not in __param_0():
        raise ValueError("Invalid email format")
    if len(__param_0()) < 5:
        raise ValueError("Email too short")

    print(f"Processing {__param_1}: {__param_0()}")
    return True


class EmailProcessor:
    """Process emails."""

    def __init__(self, email):
        self.email = email

    def process(self):
        """Process email."""
        # Validation logic
        return _extracted_func_0(lambda: self.email, 'email')


class SMSProcessor:
    """Process SMS messages."""

    def __init__(self, phone):
        self.phone = phone

    def process(self):
        """Process SMS."""
        # Validation logic (DUPLICATE!)
        return _extracted_func_0(lambda: self.phone, 'SMS')


class PushNotificationProcessor:
    """Process push notifications."""

    def __init__(self, device_id):
        self.device_id = device_id

    def process(self):
        """Process push notification."""
        # Validation logic (DUPLICATE!)
        return _extracted_func_0(lambda: self.device_id, 'push')
