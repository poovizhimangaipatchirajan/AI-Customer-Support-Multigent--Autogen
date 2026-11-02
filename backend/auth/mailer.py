"""Gmail SMTP transport. Tokens are never logged or written to disk."""
import os
import smtplib
import ssl
from email.message import EmailMessage

class MailConfigurationError(RuntimeError):
    pass

def smtp_config():
    user = os.getenv('FIXFLOW_SMTP_USER', '').strip()
    secret = os.getenv('FIXFLOW_SMTP_APP_PASSWORD', '').replace(' ', '')
    sender = os.getenv('FIXFLOW_MAIL_FROM', user).strip()
    if not user or not secret or not sender:
        raise MailConfigurationError('Gmail SMTP credentials are not configured')
    return user, secret, sender

def send_token(email, purpose, token):
    user, password, sender = smtp_config()
    if purpose not in ('verify', 'reset'):
        raise ValueError('Invalid email purpose')
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = email
    msg['Subject'] = 'NovaCart email verification' if purpose == 'verify' else 'NovaCart password reset'
    msg.set_content(('Your NovaCart verification code' if purpose == 'verify' else 'Your NovaCart password reset code') + ': ' + token + '\n\nExpires in 30 minutes. If you did not request this, ignore this email.')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
