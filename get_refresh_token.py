# get_refresh_token.py

from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail “send mail” scope
SCOPES = ['https://mail.google.com/']

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        'client_secret.json',
        scopes=SCOPES
    )
    creds = flow.run_local_server(port=0)  # launches browser for consent

    print('===')
    print('ACCESS TOKEN:', creds.token)
    print('REFRESH TOKEN:', creds.refresh_token)
    print('CLIENT ID:', creds.client_id)
    print('CLIENT SECRET:', creds.client_secret)
    print('===')

if __name__ == '__main__':
    main()
