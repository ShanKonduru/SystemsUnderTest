from billing.config import SERVICE_ROOT, load_settings


def test_staging_is_selected_with_billing_config() -> None:
    staging = load_settings({"BILLING_CONFIG": str(SERVICE_ROOT / "config" / "billing.staging.yaml")})
    assert staging.reminder_days == 1
    assert load_settings({}).reminder_days == 7
