"""Tests for the persistent Alertmanager webhook lifecycle."""

import os
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.alertmanager_webhook import (
    AlertmanagerAlert,
    AlertmanagerPayload,
    receive_alertmanager_webhook,
)
from models.core import Alert


def _payload(status: str, description: str) -> AlertmanagerPayload:
    return AlertmanagerPayload(
        version="4",
        status=status,
        receiver="foundation-platform-webhook",
        alerts=[
            AlertmanagerAlert(
                status=status,
                fingerprint="phase4-alert-fingerprint",
                labels={
                    "alertname": "foundationWebhookLifecycleTest",
                    "severity": "warning",
                    "service": "backend",
                },
                annotations={
                    "summary": "Alertmanager lifecycle test",
                    "description": description,
                },
            )
        ],
    )


class AlertmanagerWebhookTest(unittest.TestCase):
    def test_firing_retry_and_resolved_are_idempotent(self):
        with mock.patch.dict(
            os.environ, {"ALERTMANAGER_WEBHOOK_TOKEN": "unit-test-token"}
        ):
            engine = create_engine("sqlite:///:memory:")
            Alert.__table__.create(engine)
            db = sessionmaker(bind=engine)()

            created = receive_alertmanager_webhook(
                _payload("firing", "first delivery"),
                authorization="Bearer unit-test-token",
                db=db,
            )
            repeated = receive_alertmanager_webhook(
                _payload("firing", "retry delivery"),
                authorization="Bearer unit-test-token",
                db=db,
            )
            resolved = receive_alertmanager_webhook(
                _payload("resolved", "recovered"),
                authorization="Bearer unit-test-token",
                db=db,
            )

            self.assertEqual(
                created,
                {
                    "status": "accepted",
                    "received": 1,
                    "created": 1,
                    "updated": 0,
                    "resolved": 0,
                },
            )
            self.assertEqual(repeated["created"], 0)
            self.assertEqual(repeated["updated"], 1)
            self.assertEqual(resolved["resolved"], 1)

            rows = db.query(Alert).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source, "alertmanager")
            self.assertEqual(rows[0].entity_id, "phase4-alert-fingerprint")
            self.assertTrue(rows[0].is_resolved)
            self.assertIsNotNone(rows[0].resolved_at)

            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
