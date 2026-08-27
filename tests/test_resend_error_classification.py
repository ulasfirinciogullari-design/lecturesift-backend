from lecturesift.resend_diagnostics import classify_resend_error


def test_classifies_invalid_api_key_message():
    assert classify_resend_error(
        status=403,
        error_type="InternalServerError",
        message="API key is invalid.",
    ) == "invalid_api_key"


def test_classifies_unverified_domain_message():
    assert classify_resend_error(
        status=403,
        error_type="InternalServerError",
        message="The mail.example.com domain is not verified. Please add and verify your domain.",
    ) == "domain_not_verified"


def test_classifies_test_recipient_restriction():
    assert classify_resend_error(
        status=403,
        error_type="validation_error",
        message="You can only send testing emails to your own email address.",
    ) == "testing_recipient_restricted"


def test_preserves_known_non_generic_provider_type():
    assert classify_resend_error(
        status=429,
        error_type="rate_limit_exceeded",
        message="Too many requests.",
    ) == "rate_limit_exceeded"


def test_unknown_403_is_reduced_to_non_secret_code():
    secret_detail = "Customer-specific gateway message with internal request data"
    code = classify_resend_error(
        status=403,
        error_type="InternalServerError",
        message=secret_detail,
    )
    assert code == "forbidden_unknown"
    assert secret_detail not in code
