import pytest

from insurance_copilot.backend.db import AccessDenied, InsuranceBackend, InvalidRequest, NotFound
from tests.conftest import TODAY


def test_quote_then_purchase_creates_policy(db):
    be = InsuranceBackend(db, "CUST-003", TODAY)
    quote = be.create_quote("RENTERS", "standard", 500,
                            {"property_address": "201 River St", "personal_property_value": "30000"})
    assert quote["quote_id"] == "QTE-7001" and quote["monthly_premium"] == 18.0
    policy = be.purchase_policy(quote["quote_id"])
    assert policy["id"] == "POL-2003" and policy["status"] == "active" and policy["balance_due"] == 18.0
    with pytest.raises(InvalidRequest):  # a quote can only be used once
        be.purchase_policy(quote["quote_id"])


def test_quote_reports_missing_fields(db):
    be = InsuranceBackend(db, "CUST-003", TODAY)
    with pytest.raises(InvalidRequest, match="vehicle_make"):
        be.create_quote("AUTO", "standard", 500, {"vehicle_year": "2020"})


def test_other_customers_policy_is_denied(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    for call in (lambda: be.get_policy("POL-2001"), lambda: be.cancel_policy("POL-2001", "x"),
                 lambda: be.make_payment("POL-2001", 1, "card_on_file")):
        with pytest.raises(AccessDenied):
            call()


def test_other_customers_quote_cannot_be_purchased(db):
    q = InsuranceBackend(db, "CUST-002", TODAY).create_quote(
        "TRAVEL", "basic", 250, {"destination": "Rome", "trip_start": "2026-12-01", "trip_end": "2026-12-10"})
    with pytest.raises(AccessDenied):
        InsuranceBackend(db, "CUST-001", TODAY).purchase_policy(q["quote_id"])


def test_payment_cannot_exceed_balance(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest):
        be.make_payment("POL-1002", 1000, "card_on_file")
    assert be.make_payment("POL-1002", 193.6, "card_on_file")["remaining_balance"] == 0


def test_coverage_change_reprices(db):
    out = InsuranceBackend(db, "CUST-001", TODAY).update_coverage("POL-1001", deductible=1000)
    assert out["old"]["monthly_premium"] == 95.0 and out["new"]["monthly_premium"] == 83.6


def test_claim_rules(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest):
        be.file_claim("POL-1001", "2026-12-01", "collision", "future", 100)
    claim = be.file_claim("POL-1001", "2026-11-02", "collision", "bumper", 1800)
    assert claim["claim_id"] == "CLM-5003"


def test_add_driver_reprices_and_is_listed(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    out = be.add_driver("POL-1001", "Sri", "2008-05-14", "child")   # 18 years old -> +35%
    assert out["driver_id"] == "DRV-3002" and out["age"] == 18          # DRV-3001 is seeded on POL-2001
    assert out["old_monthly_premium"] == 95.0 and out["new_monthly_premium"] == 128.25
    drivers = be.list_drivers("POL-1001")
    assert drivers["primary_driver"]["full_name"] == "Shan"
    assert [d["full_name"] for d in drivers["additional_drivers"]] == ["Sri"]
    # a later coverage change keeps the driver surcharge
    assert be.update_coverage("POL-1001", deductible=1000)["new"]["monthly_premium"] == round(83.6 * 1.35, 2)


def test_add_driver_rules(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="at least 16"):
        be.add_driver("POL-1001", "Kid", "2012-01-01", "child")
    with pytest.raises(InvalidRequest, match="not an auto policy"):
        be.add_driver("POL-1002", "Sri", "2008-05-14", "child")
    with pytest.raises(InvalidRequest, match="relationship"):
        be.add_driver("POL-1001", "Sri", "2008-05-14", "cousin")
    with pytest.raises(AccessDenied):
        be.add_driver("POL-2001", "Sri", "2008-05-14", "child")
    be.add_driver("POL-1001", "Sri", "2008-05-14", "child")
    with pytest.raises(InvalidRequest, match="already a driver"):
        be.add_driver("POL-1001", "sri", "2008-05-14", "child")


def test_existing_database_gets_drivers_table(db):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("DROP TABLE drivers")
    con.commit()
    con.close()
    assert InsuranceBackend(db, "CUST-001", TODAY).list_drivers("POL-1001")["additional_drivers"] == []


def test_rider_eligibility_by_policy(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    home = {r["code"]: r for r in be.list_riders("POL-1002")["riders"]}
    assert home["FLOOD"]["eligible"] and home["FLOOD"]["monthly_cost"] == 22.0
    assert not home["RENTAL_CAR"]["eligible"] and "AUTO" in home["RENTAL_CAR"]["not_eligible_reason"]
    assert "your AUTO policy POL-1001 instead" in home["RENTAL_CAR"]["not_eligible_reason"]
    by_product = be.list_riders(product_code="HOME")            # one HOME policy -> answered for it
    assert by_product["policy_id"] == "POL-1002"
    assert "riders" in InsuranceBackend(db, "CUST-003", TODAY).list_riders(product_code="HOME")  # no policy: catalog
    auto = {r["code"]: r for r in be.list_riders("POL-1001")["riders"]}
    assert auto["RENTAL_CAR"]["eligible"] and auto["ROADSIDE"]["eligible"]
    assert not auto["GAP"]["eligible"] and "2019" in auto["GAP"]["not_eligible_reason"]   # car too old
    assert not auto["FLOOD"]["eligible"]
    riders = be.list_riders("POL-1002")["riders"]
    assert all(r["eligible"] for r in riders[:6]) and not riders[-1]["eligible"]          # eligible first
    # newer car: gap cover is allowed
    assert {r["code"]: r for r in InsuranceBackend(db, "CUST-002", TODAY).list_riders("POL-2001")["riders"]}[
        "GAP"]["eligible"]


def test_add_and_remove_rider_reprices(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    out = be.add_rider("POL-1002", "flood")
    assert out["old_monthly_premium"] == 106.48 and out["new_monthly_premium"] == 128.48
    assert out["coverage_starts"] == "2026-12-03"                                         # 30-day waiting period
    assert [r["rider_code"] for r in be.get_policy("POL-1002")["riders"]] == ["FLOOD"]
    with pytest.raises(InvalidRequest, match="already on"):
        be.add_rider("POL-1002", "FLOOD")
    with pytest.raises(InvalidRequest, match="only available for AUTO"):
        be.add_rider("POL-1002", "RENTAL_CAR")
    with pytest.raises(AccessDenied):
        be.add_rider("POL-2002", "FLOOD")
    assert be.remove_rider("POL-1002", "FLOOD")["new_monthly_premium"] == 106.48


def test_riders_and_drivers_combine_in_premium(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    be.add_driver("POL-1001", "Sri", "2008-05-14", "child")            # 95 * 1.35 = 128.25
    assert be.add_rider("POL-1001", "ROADSIDE")["new_monthly_premium"] == 132.25
    be.add_rider("POL-1001", "RENTAL_CAR")
    with pytest.raises(InvalidRequest, match="Rental Car Cover requires"):
        be.update_coverage("POL-1001", coverage_level="basic")


PDF = b"%PDF-1.4\n% proof\n%%EOF\n"


def test_document_upload_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    doc = be.store_document(r"C:\scans\bill.pdf", "application/pdf", PDF)
    assert doc["document_id"] == "DOC-6001" and doc["filename"] == "bill.pdf"
    with pytest.raises(InvalidRequest, match="does not look like"):
        be.store_document("fake.pdf", "application/pdf", b"MZ not a pdf")
    with pytest.raises(InvalidRequest, match="Only PDF"):
        be.store_document("notes.txt", "text/plain", b"hello")
    with pytest.raises(AccessDenied):
        InsuranceBackend(db, "CUST-002", TODAY).get_document("DOC-6001")


def test_address_change_requires_matching_proof(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    req = be.change_requirements("mailing_address")
    assert "utility_bill" in req["accepted_documents"] and req["current_value"].startswith("48 Oak Ave")
    doc = be.store_document("bill.pdf", "application/pdf", PDF)["document_id"]
    with pytest.raises(InvalidRequest, match="not accepted as proof"):
        be.submit_profile_change("mailing_address", "77 Lake Shore Dr, Chicago, IL 60611", doc, "passport",
                                 effective_date="2026-10-20")
    with pytest.raises(NotFound):
        be.submit_profile_change("mailing_address", "77 Lake Shore Dr, Chicago, IL 60611", "DOC-9999", "utility_bill",
                                 effective_date="2026-10-20")
    out = be.submit_profile_change("mailing_address", "77 Lake Shore Dr, Chicago, IL 60611", doc, "utility_bill",
                                   reason="moved", effective_date="2026-10-20")
    assert out["change_id"] == "CHG-8001" and out["old_value"].startswith("48 Oak Ave")
    assert be.get_profile()["address"] == "77 Lake Shore Dr, Chicago, IL 60611"
    assert be.list_documents()[0]["used_for_change"] == "CHG-8001"
    with pytest.raises(InvalidRequest, match="already used"):   # one proof, one change
        be.submit_profile_change("mailing_address", "1 Main St, Springfield, IL 62701", doc, "utility_bill",
                                 effective_date="2026-11-01")
    doc2 = be.store_document("lease.pdf", "application/pdf", PDF)["document_id"]
    with pytest.raises(InvalidRequest, match="effective_date"):  # move date is a required follow-up
        be.submit_profile_change("mailing_address", "1 Main St, Springfield, IL 62701", doc2, "lease_agreement")


def test_dob_change_rules(db):
    be = InsuranceBackend(db, "CUST-002", TODAY)
    doc = be.store_document("passport.png", "image/png", b"\x89PNG\r\n\x1a\n" + bytes(16))["document_id"]
    with pytest.raises(InvalidRequest, match="not accepted"):
        be.submit_profile_change("date_of_birth", "1990-10-03", doc, "utility_bill", reason="typo")
    with pytest.raises(InvalidRequest, match="reason"):         # reason is a required follow-up
        be.submit_profile_change("date_of_birth", "1990-10-03", doc, "passport")
    with pytest.raises(ValueError):
        be.submit_profile_change("date_of_birth", "October 3rd", doc, "passport", reason="typo")
    other = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(AccessDenied):                            # someone else's document
        other.submit_profile_change("date_of_birth", "1985-04-13", doc, "passport", reason="typo")
    assert be.submit_profile_change("date_of_birth", "1990-10-03", doc, "passport",
                                    reason="typo")["new_value"] == "1990-10-03"


def test_remove_driver_reprices_and_keeps_history(db):
    import sqlite3
    be = InsuranceBackend(db, "CUST-002", TODAY)
    assert be.get_policy("POL-2001")["monthly_premium"] == 198.68            # base 176.98 x 1.10 (Kiran) + 4 roadside
    out = be.remove_driver("POL-2001", "drv-3001")
    assert out["full_name"] == "Kiran" and out["new_monthly_premium"] == 180.98
    assert be.list_drivers("POL-2001")["additional_drivers"] == []
    row = sqlite3.connect(db).execute("SELECT status, removed_on FROM drivers WHERE id='DRV-3001'").fetchone()
    assert row == ("removed", "2026-11-03")                                  # kept as history
    with pytest.raises(NotFound, match="not an active driver"):
        be.remove_driver("POL-2001", "DRV-3001")
    # removed drivers don't block re-adding the same person
    assert be.add_driver("POL-2001", "Kiran", "1988-02-11", "spouse")["new_monthly_premium"] == 198.68


def test_remove_driver_rules(db):
    with pytest.raises(AccessDenied):
        InsuranceBackend(db, "CUST-001", TODAY).remove_driver("POL-2001", "DRV-3001")
    with pytest.raises(InvalidRequest, match="not an auto policy"):
        InsuranceBackend(db, "CUST-002", TODAY).remove_driver("POL-2002", "DRV-3001")


def test_remove_seeded_rider(db):
    be = InsuranceBackend(db, "CUST-002", TODAY)
    out = be.remove_rider("POL-2001", "roadside")
    assert out["old_monthly_premium"] == 198.68 and out["new_monthly_premium"] == 194.68
    assert be.get_policy("POL-2001")["riders"] == []
    with pytest.raises(NotFound):
        be.remove_rider("POL-2001", "ROADSIDE")


def test_old_drivers_table_is_migrated(db):
    import sqlite3
    con = sqlite3.connect(db)
    con.executescript("DROP TABLE drivers; CREATE TABLE drivers (id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, "
                      "customer_id TEXT NOT NULL, full_name TEXT NOT NULL, date_of_birth TEXT NOT NULL, "
                      "relationship TEXT NOT NULL, added_on TEXT NOT NULL); "
                      "INSERT INTO drivers VALUES ('DRV-3001','POL-2001','CUST-002','Kiran','1988-02-11','spouse','2026-05-17');")
    con.close()
    be = InsuranceBackend(db, "CUST-002", TODAY)
    assert [d["driver_id"] for d in be.list_drivers("POL-2001")["additional_drivers"]] == ["DRV-3001"]
    assert be.remove_driver("POL-2001", "DRV-3001")["status"] == "removed"
