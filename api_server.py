"""
Flask API Server for Zazy Playlist Automation
Provides HTTP endpoints to trigger the automation script and handle callbacks.
"""

import os
import sys
import subprocess
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration
API_KEY = os.getenv("API_KEY", "your-secret-api-key-here")
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "zazy_playlist_automation.py")


def verify_api_key():
    """Verify the API key from request headers."""
    auth_header = request.headers.get('Authorization')

    print(f"[DEBUG] Authorization header received: {auth_header[:20] if auth_header else 'None'}...")
    print(f"[DEBUG] Expected API_KEY (first 20 chars): {API_KEY[:20] if API_KEY else 'None'}...")

    if not auth_header:
        print("[DEBUG] No Authorization header provided")
        return False

    # Support both "Bearer TOKEN" and "TOKEN" formats
    token = auth_header.replace('Bearer ', '').strip()

    print(f"[DEBUG] Extracted token (first 20 chars): {token[:20]}...")
    print(f"[DEBUG] Tokens match: {token == API_KEY}")

    return token == API_KEY


def run_automation_script(user_id=None, callback_url=None):
    """
    Run the automation script in a separate process.

    Args:
        user_id: Optional Laravel IPTV account ID
        callback_url: Optional callback URL to send results to
    """
    try:
        # Build command
        cmd = [sys.executable, SCRIPT_PATH]

        if user_id:
            cmd.extend(['--user-id', str(user_id)])

        if callback_url:
            cmd.extend(['--callback-url', callback_url])

        print(f"[*] Running command: {' '.join(cmd)}")

        # Run the script
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900  # 15 minute timeout
        )

        print(f"[*] Script completed with return code: {result.returncode}")
        print(f"[*] STDOUT: {result.stdout[-500:]}")  # Last 500 chars

        if result.returncode != 0:
            print(f"[!] STDERR: {result.stderr[-500:]}")

        return {
            'success': result.returncode == 0,
            'return_code': result.returncode,
            'output': result.stdout[-1000:] if result.stdout else None,
            'error': result.stderr[-1000:] if result.stderr else None
        }

    except subprocess.TimeoutExpired:
        print("[!] Script execution timed out after 15 minutes")
        return {
            'success': False,
            'error': 'Script execution timed out after 15 minutes'
        }
    except Exception as e:
        print(f"[!] Error running script: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'service': 'zazy-automation-api',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/generate', methods=['POST'])
def generate_account():
    """
    Trigger Zazy account generation.

    Request body:
    {
        "user_id": 123,  // Optional - Laravel IPTV account ID
        "callback_url": "https://your-app.com/api/webhooks/zazy-automation"  // Optional
    }

    Response:
    {
        "status": "started",
        "message": "Automation script started in background",
        "user_id": 123
    }
    """
    # Verify API key
    if not verify_api_key():
        return jsonify({'error': 'Unauthorized - Invalid API key'}), 401

    try:
        data = request.get_json() or {}
        user_id = data.get('user_id')
        callback_url = data.get('callback_url')

        print(f"[*] Received generation request: user_id={user_id}, callback_url={callback_url}")

        # Run script in background thread
        def run_in_background():
            run_automation_script(user_id=user_id, callback_url=callback_url)

        thread = threading.Thread(target=run_in_background, daemon=True)
        thread.start()

        return jsonify({
            'status': 'started',
            'message': 'Automation script started in background. Results will be sent to callback URL if provided.',
            'user_id': user_id,
            'estimated_time': '2-8 minutes'
        }), 202  # 202 Accepted

    except Exception as e:
        print(f"[!] Error in generate_account endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate/sync', methods=['POST'])
def generate_account_sync():
    """
    Trigger Zazy account generation synchronously (waits for completion).
    Use this for testing or when immediate results are needed.
    WARNING: This endpoint may take 2-8 minutes to respond.

    Request body:
    {
        "user_id": 123,  // Optional
        "callback_url": "https://your-app.com/api/webhooks/zazy-automation"  // Optional
    }

    Response:
    {
        "status": "completed|failed",
        "result": {...}  // Script execution details
    }
    """
    # Verify API key
    if not verify_api_key():
        return jsonify({'error': 'Unauthorized - Invalid API key'}), 401

    try:
        data = request.get_json() or {}
        user_id = data.get('user_id')
        callback_url = data.get('callback_url')

        print(f"[*] Received sync generation request: user_id={user_id}, callback_url={callback_url}")

        # Run script synchronously
        result = run_automation_script(user_id=user_id, callback_url=callback_url)

        return jsonify({
            'status': 'completed' if result['success'] else 'failed',
            'result': result
        }), 200 if result['success'] else 500

    except Exception as e:
        print(f"[!] Error in generate_account_sync endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get API status and configuration."""
    # Verify API key
    if not verify_api_key():
        return jsonify({'error': 'Unauthorized - Invalid API key'}), 401

    return jsonify({
        'status': 'running',
        'script_path': SCRIPT_PATH,
        'script_exists': os.path.exists(SCRIPT_PATH),
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8899))
    host = os.getenv('HOST', '0.0.0.0')
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    print(f"[*] Starting Zazy Automation API on {host}:{port}")
    print(f"[*] Script path: {SCRIPT_PATH}")
    print(f"[*] Debug mode: {debug}")

    app.run(host=host, port=port, debug=debug)
