import pytest
from fastapi import HTTPException
from backend.main import validate_registration


def test_registration_valid():
    email, name, phone = validate_registration(' Test@Example.COM ', 'GoodPassword!234', '  Asha   Kumar ', '9876543210')
    assert (email, name, phone) == ('test@example.com', 'Asha Kumar', '+919876543210')


@pytest.mark.parametrize('email', ['not-an-email', 'abc@', 'a b@example.com'])
def test_invalid_email(email):
    with pytest.raises(HTTPException):
        validate_registration(email, 'GoodPassword!234', 'Asha Kumar')


@pytest.mark.parametrize('password', ['short', 'alllowercase123!', 'ALLUPPERCASE123!', 'NoNumbersHere!', 'NoSpecial1234'])
def test_weak_password(password):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', password, 'Asha Kumar')


@pytest.mark.parametrize('name', ['', 'A', '12345', '   '])
def test_invalid_name(name):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', 'GoodPassword!234', name)


@pytest.mark.parametrize('phone', ['12345', '5123456789', '+441234567890'])
def test_invalid_phone(phone):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', 'GoodPassword!234', 'Asha Kumar', phone)


def test_legacy_api_registration_payload_supported():
    assert validate_registration('test@example.com', 'GoodPassword!234') == ('test@example.com', None, None)
