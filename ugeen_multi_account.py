#!/usr/bin/env python3
"""
UGEEN Multi-Account Automation Wrapper
======================================
This script runs UGEEN account renewals for multiple accounts sequentially.
Each account is processed independently with its own session management.

Usage:
    python ugeen_multi_account.py

Configuration:
    Set UGEEN_ACCOUNT_X_EMAIL and UGEEN_ACCOUNT_X_PASSWORD in .env file
"""

import os
import sys
import subprocess
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def log_message(message):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_ugeen_account(account_num, email, password):
    """
    Run UGEEN automation for a single account

    Args:
        account_num: Account number (for logging)
        email: UGEEN account email
        password: UGEEN account password

    Returns:
        bool: True if successful, False otherwise
    """
    log_message(f"{'='*60}")
    log_message(f"Starting UGEEN Account {account_num}: {email}")
    log_message(f"{'='*60}")

    try:
        # Run ugeen_api_scraper.py with account-specific credentials
        cmd = [
            sys.executable,  # Use the same Python interpreter
            '/app/ugeen_api_scraper.py',
            '--username', email,
            '--password', password
        ]

        log_message(f"Executing: {' '.join(cmd[:-2])} --username {email} --password ****")

        # Run the command and capture output
        result = subprocess.run(
            cmd,
            cwd='/app',
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout per account
        )

        # Print stdout
        if result.stdout:
            print(result.stdout, flush=True)

        # Print stderr if there are errors
        if result.stderr:
            print(result.stderr, file=sys.stderr, flush=True)

        if result.returncode == 0:
            log_message(f"✓ Account {account_num} ({email}) completed successfully")
            return True
        else:
            log_message(f"✗ Account {account_num} ({email}) failed with exit code {result.returncode}")
            return False

    except subprocess.TimeoutExpired:
        log_message(f"✗ Account {account_num} ({email}) timed out after 10 minutes")
        return False
    except Exception as e:
        log_message(f"✗ Account {account_num} ({email}) encountered error: {str(e)}")
        return False


def main():
    """Main execution function"""
    log_message("UGEEN Multi-Account Automation Started")
    log_message(f"Python: {sys.version}")
    log_message(f"Working Directory: {os.getcwd()}")

    # Collect all configured accounts
    accounts = []
    account_num = 1

    while True:
        email = os.getenv(f'UGEEN_ACCOUNT_{account_num}_EMAIL')
        password = os.getenv(f'UGEEN_ACCOUNT_{account_num}_PASSWORD')

        if not email or not password:
            break

        accounts.append({
            'num': account_num,
            'email': email,
            'password': password
        })
        account_num += 1

    if not accounts:
        log_message("ERROR: No UGEEN accounts found in environment variables")
        log_message("Please set UGEEN_ACCOUNT_1_EMAIL, UGEEN_ACCOUNT_1_PASSWORD, etc. in .env")
        sys.exit(1)

    log_message(f"Found {len(accounts)} UGEEN account(s) to process")

    # Process each account sequentially
    results = []
    for account in accounts:
        success = run_ugeen_account(
            account['num'],
            account['email'],
            account['password']
        )
        results.append({
            'account': account['num'],
            'email': account['email'],
            'success': success
        })

        # Add delay between accounts to avoid rate limiting
        if account != accounts[-1]:  # Don't delay after last account
            delay = 30  # 30 seconds between accounts
            log_message(f"Waiting {delay} seconds before processing next account...")
            time.sleep(delay)

    # Print summary
    log_message("")
    log_message("="*60)
    log_message("UGEEN Multi-Account Automation Summary")
    log_message("="*60)

    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful

    for result in results:
        status = "✓ SUCCESS" if result['success'] else "✗ FAILED"
        log_message(f"Account {result['account']} ({result['email']}): {status}")

    log_message("")
    log_message(f"Total: {len(results)} accounts | Success: {successful} | Failed: {failed}")

    # Exit with error code if any account failed
    if failed > 0:
        log_message("WARNING: Some accounts failed to process")
        sys.exit(1)
    else:
        log_message("All accounts processed successfully!")
        sys.exit(0)


if __name__ == '__main__':
    main()
