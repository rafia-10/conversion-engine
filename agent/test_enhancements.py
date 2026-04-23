import os
import time
import requests
import unittest
from multiprocessing import Process
from agent.webhook_server import app
from agent.events import events
from agent.sms_handler import AfricaTalkingClient
from agent.calendar_integration import CalComClient # To ensure it's importable

class TestEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start webhook server in a separate process
        cls.server_process = Process(target=app.run, kwargs={"port": 5001, "debug": False, "use_reloader": False})
        cls.server_process.start()
        time.sleep(2) # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.join()

    def test_resend_webhook_parsing(self):
        received_data = []
        def callback(data):
            received_data.append(data)
        
        events.on("email_reply", callback)
        
        payload = {
            "type": "email.replied",
            "data": {
                "from": "prospect@example.com",
                "to": "sales@tenacious.consulting",
                "subject": "Re: Discovery",
                "text": "Let's talk!",
                "id": "email_123"
            }
        }
        
        response = requests.post("http://localhost:5001/webhooks/resend", json=payload)
        self.assertEqual(response.status_code, 200)
        time.sleep(0.5)
        
        self.assertEqual(len(received_data), 1)
        self.assertEqual(received_data[0]["from"], "prospect@example.com")
        self.assertEqual(received_data[0]["text"], "Let's talk!")

    def test_africastalking_webhook_parsing(self):
        received_data = []
        def callback(data):
            received_data.append(data)
        
        events.on("sms_reply", callback)
        
        # Africa's Talking form-encoded
        payload = {
            "from": "+254711223344",
            "to": "22345",
            "text": "YES",
            "date": "2023-10-01",
            "id": "sms_abc"
        }
        
        response = requests.post("http://localhost:5001/webhooks/africastalking", data=payload)
        self.assertEqual(response.status_code, 200)
        time.sleep(0.5)
        
        self.assertEqual(len(received_data), 1)
        self.assertEqual(received_data[0]["from"], "+254711223344")
        self.assertEqual(received_data[0]["text"], "YES")

    def test_sms_gating_logic(self):
        # This test mock HubSpot responses
        client = AfricaTalkingClient()
        
        # Mock is_warm_lead to return False
        def mock_is_warm_lead_cold(to): return False
        client.is_warm_lead = mock_is_warm_lead_cold
        
        result = client.send_sms("+123456789", "Hello")
        self.assertEqual(result["status"], "gate_blocked")
        
        # Mock is_warm_lead to return True
        def mock_is_warm_lead_warm(to): return True
        client.is_warm_lead = mock_is_warm_lead_warm
        
        # This will still fail at the network level because of missing API key or real endpoint
        # but we can check if it passed the gate
        result = client.send_sms("+123456789", "Hello")
        self.assertNotEqual(result.get("status"), "gate_blocked")

if __name__ == "__main__":
    unittest.main()
