# test_create_ticket.py
"""
Utility script to test ticket creation via the Bosowa portal API.

Usage:
    python test_create_ticket.py <JWT_TOKEN>

It sends a POST request to the Bosowa portal API /api/tickets with a sample payload.
"""
import sys
import json
import os
import requests

API_URL = os.getenv("BOSOWA_SERVER_URL", "http://localhost:3000") + "/api/tickets"

SAMPLE_TICKET = {
    "title": "PC tidak bisa connect internet",
    "category": "Jaringan",
    "description": "Sejak pagi tidak bisa akses browser, tapi WiFi terkoneksi.",
    "priority": "HIGH",
    "employee_id": "EMP-0001",
    "employee_name": "Budi Santoso",
    "device_mac": "AA:BB:CC:DD:EE:01"
}

def create_ticket(token: str) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, headers=headers, data=json.dumps(SAMPLE_TICKET))
    if response.ok:
        print("✅ Ticket created successfully! Response:")
        print(response.json())
    else:
        print(f"❌ Failed to create ticket (status {response.status_code})")
        print(response.text)

if __name__ == "__main__":
    if len(sys.argv) == 2:
        token = sys.argv[1]
    else:
        token = os.getenv("JWT_TOKEN")
        if not token:
            print("Usage: python test_create_ticket.py <JWT_TOKEN> or set JWT_TOKEN env var")
            sys.exit(1)
    create_ticket(token)
