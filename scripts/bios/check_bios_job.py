import requests, urllib3, json
urllib3.disable_warnings()
base = 'https://192.168.10.5'
auth = ('root', 'Tricolor00!')
r = requests.get(f'{base}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs/JID_730931826679', auth=auth, verify=False, timeout=15)
j = r.json()
for key in ['JobState', 'JobType', 'PercentComplete', 'Message', 'MessageId', 'Name', 'StartTime', 'EndTime']:
    print(f'{key}: {j.get(key)}')
