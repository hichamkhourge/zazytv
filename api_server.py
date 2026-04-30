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
UGEEN_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "ugeen_api_scraper.py")
UGEEN_RENEW_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "ugeen_renew_user.py")


def verify_api_key():
    """Verify the API key from request headers."""
    auth_header = request.headers.get('Authorization')

    print("\n" + "="*60)
    print("API KEY VERIFICATION DEBUG")
    print("="*60)

    if auth_header:
        print(f"[DEBUG] Full Authorization header: {auth_header}")
        print(f"[DEBUG] Header length: {len(auth_header)}")
    else:
        print("[DEBUG] Authorization header: MISSING")

    print(f"[DEBUG] Expected API_KEY: {API_KEY}")
    print(f"[DEBUG] Expected length: {len(API_KEY)}")
    print(f"[DEBUG] Expected first 10 chars: {API_KEY[:10]}")
    print(f"[DEBUG] Expected last 10 chars: {API_KEY[-10:]}")

    if not auth_header:
        print("[DEBUG] Result: FAILED - No Authorization header")
        print("="*60 + "\n")
        return False

    # Support both "Bearer TOKEN" and "TOKEN" formats
    token = auth_header.replace('Bearer ', '').strip()

    print(f"[DEBUG] Extracted token: {token}")
    print(f"[DEBUG] Extracted length: {len(token)}")
    print(f"[DEBUG] Extracted first 10 chars: {token[:10]}")
    print(f"[DEBUG] Extracted last 10 chars: {token[-10:]}")
    print(f"[DEBUG] Tokens match: {token == API_KEY}")

    if token != API_KEY:
        print(f"[DEBUG] Character-by-character comparison:")
        for i, (c1, c2) in enumerate(zip(token, API_KEY)):
            if c1 != c2:
                print(f"[DEBUG]   Position {i}: got '{c1}' (ord {ord(c1)}), expected '{c2}' (ord {ord(c2)})")
        if len(token) != len(API_KEY):
            print(f"[DEBUG]   Length mismatch: got {len(token)}, expected {len(API_KEY)}")
        print("[DEBUG] Result: FAILED - Tokens don't match")
    else:
        print("[DEBUG] Result: SUCCESS - Tokens match")

    print("="*60 + "\n")

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


def run_ugeen_script(user_id=None, callback_url=None, username=None, password=None):
    """
    Run the Ugeen automation script in a separate process.

    Args:
        user_id: Optional Laravel IPTV account ID
        callback_url: Optional callback URL to send results to
        username: Optional Ugeen master account username (overrides env)
        password: Optional Ugeen master account password (overrides env)
    """
    try:
        # Build command
        cmd = [sys.executable, UGEEN_SCRIPT_PATH]

        if user_id:
            cmd.extend(['--user-id', str(user_id)])

        if callback_url:
            cmd.extend(['--callback-url', callback_url])

        if username:
            cmd.extend(['--username', username])

        if password:
            cmd.extend(['--password', password])

        print(f"[*] Running Ugeen command: {' '.join(cmd[:4])}... (credentials hidden)")

        # Run the script
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900  # 15 minute timeout
        )

        print(f"[*] Ugeen script completed with return code: {result.returncode}")
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
        print("[!] Ugeen script execution timed out after 15 minutes")
        return {
            'success': False,
            'error': 'Script execution timed out after 15 minutes'
        }
    except Exception as e:
        print(f"[!] Error running Ugeen script: {e}")
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
    # Verify API key - TEMPORARILY DISABLED FOR TESTING
    # if not verify_api_key():
    #     return jsonify({'error': 'Unauthorized - Invalid API key'}), 401
    print("[WARNING] API key verification is DISABLED - for testing only!")

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
    # Verify API key - TEMPORARILY DISABLED FOR TESTING
    # if not verify_api_key():
    #     return jsonify({'error': 'Unauthorized - Invalid API key'}), 401
    print("[WARNING] API key verification is DISABLED - for testing only!")

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
    # Verify API key - TEMPORARILY DISABLED FOR TESTING
    # if not verify_api_key():
    #     return jsonify({'error': 'Unauthorized - Invalid API key'}), 401
    print("[WARNING] API key verification is DISABLED - for testing only!")

    return jsonify({
        'status': 'running',
        'script_path': SCRIPT_PATH,
        'script_exists': os.path.exists(SCRIPT_PATH),
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/generate/ugeen', methods=['POST'])
def generate_ugeen():
    """
    Trigger Ugeen account generation or renewal.

    Request body:
    {
        "user_id": 123,  // Required - Laravel IPTV account ID
        "callback_url": "https://your-app.com/api/webhooks/ugeen-automation",  // Required
        "username": "master@email.com",  // Optional - Ugeen master username (overrides env)
        "password": "master_password"  // Optional - Ugeen master password (overrides env)
    }

    Response:
    {
        "status": "started",
        "message": "Ugeen automation script started in background",
        "user_id": 123
    }
    """
    # Verify API key - TEMPORARILY DISABLED FOR TESTING
    # if not verify_api_key():
    #     return jsonify({'error': 'Unauthorized - Invalid API key'}), 401
    print("[WARNING] API key verification is DISABLED - for testing only!")

    try:
        data = request.get_json() or {}
        user_id = data.get('user_id')
        callback_url = data.get('callback_url')
        username = data.get('username')  # Optional - overrides env
        password = data.get('password')  # Optional - overrides env

        print(f"[*] Received Ugeen request: user_id={user_id}, callback_url={callback_url}, custom_creds={bool(username and password)}")

        # Run script in background thread
        def run_in_background():
            run_ugeen_script(
                user_id=user_id,
                callback_url=callback_url,
                username=username,
                password=password
            )

        thread = threading.Thread(target=run_in_background, daemon=True)
        thread.start()

        return jsonify({
            'status': 'started',
            'message': 'Ugeen automation script started in background. Progress updates and results will be sent to callback URL.',
            'user_id': user_id,
            'estimated_time': '3-10 minutes'
        }), 202  # 202 Accepted

    except Exception as e:
        print(f"[!] Error in generate_ugeen endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8899))
    host = os.getenv('HOST', '0.0.0.0')
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    print(f"[*] Starting Zazy Automation API on {host}:{port}")
    print(f"[*] Script path: {SCRIPT_PATH}")
    print(f"[*] Debug mode: {debug}")

    app.run(host=host, port=port, debug=debug)
