import argparse
import http.client
import re
import time
import traceback
from calendar import timegm
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from math import floor

import boto3
import requests
import yt_dlp
import yt_dlp.cookies
from jose import jwt
from requests import PreparedRequest
from requests import RequestException
from requests import Response
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from yt_dlp.cookies import YoutubeDLCookieJar


def download(media_id: str, *, media_type: str, ytdlp_options: dict = None):
    assert None not in extract_cookies_from_browser(), 'Please make sure you have logged into Wrexham AFC in Chrome.'

    if media_id in ['live', None]:

        live_event = load_live_event()
        live_media = None

        for media in live_event['itemData']:
            if media['metaData']['media_type'] == media_type:
                live_media = media
                break

        assert live_media is not None, 'Unable to find live media.'
        assert live_media['mediaData']['mediaType'] == 'Live', 'Unable to find live media.'

        live_media_id = live_media['mediaData']['entryId']
        assert live_media_id is not None, 'Unable to find live media ID.'

        print(f"{live_media['metaData']['title']} ({live_media_id})")

        schedule_start = live_event['scheduleData']['start']
        schedule_start = datetime.strptime(schedule_start, '%Y-%m-%dT%H:%M:%S.%fZ')
        schedule_start = schedule_start.replace(tzinfo=timezone.utc)

        schedule_end = live_event['scheduleData']['end']
        schedule_end = datetime.strptime(schedule_end, '%Y-%m-%dT%H:%M:%S.%fZ')
        schedule_end = schedule_end.replace(tzinfo=timezone.utc)

        print(f"Scheduled start: {schedule_start.astimezone().strftime('%B %d, %-I:%M %p %Z')}")
        print(f"Scheduled end: {schedule_end.astimezone().strftime('%B %d, %-I:%M %p %Z')}")
        print(f"Current time: {datetime.now(tz=timezone.utc).astimezone().strftime('%B %d, %-I:%M %p %Z')}")

        assert datetime.now(tz=timezone.utc) < schedule_end, 'Event has already ended.'

        adjusted_start = schedule_start - timedelta(minutes=5)  # start 5 minutes early

        strfunit = lambda i, unit: f"{floor(i)} {unit}{'' if floor(i) == 1 else 's'}"

        while adjusted_start > datetime.now(tz=timezone.utc):
            wait_remainder = (adjusted_start - datetime.now(tz=timezone.utc)).seconds
            wait_hours, wait_remainder = divmod(wait_remainder, 3600)
            wait_minutes, wait_seconds = divmod(wait_remainder, 60)

            print(f"Waiting for event to start... {strfunit(wait_hours, 'hour')}, {strfunit(wait_minutes, 'minute')}, {strfunit(wait_seconds, 'second')}")

            time.sleep(  # determines countdown interval based on remaining time
                # 1s if <= 10s left
                1 if wait_hours == 0 and wait_minutes == 0 and wait_seconds <= 10 else
                # just enough time to reach 10s left if between 11-20s
                (wait_seconds - 10) if wait_hours == 0 and wait_minutes == 0 and wait_seconds <= 20 else
                # 10s if > 20s but within 1m
                10 if wait_hours == 0 and wait_minutes == 0 else
                # 1m if <= 10m left
                60 if wait_hours == 0 and wait_minutes <= 10 else
                # just enough time to reach 10m left if between 11-20m
                (wait_minutes - 10) * 60 if wait_hours == 0 and wait_minutes <= 20 else
                # 10m if > 20m but within 1h
                600 if wait_hours == 0 else
                # 1h otherwise
                3600
            )

        media_id = live_media_id

    assert media_id is not None, 'Unable to find media ID.'

    while True:

        metadata, headers = prepare_session(media_id)

        filename = f"{metadata['name']}.ts"
        filename = filename.replace('/', '_')

        try:

            with yt_dlp.YoutubeDL(dict(
                    http_headers=headers,
                    outtmpl=dict(default=filename),
                    hls_use_mpegts=True,
                    **ytdlp_options,
            )) as ydl:
                ydl.download([metadata['media']['hls']])

            break

        except yt_dlp.utils.DownloadError as e:
            if 'ffmpeg exited with code 8' in str(e):
                time.sleep(10)
                continue
            # raise  # Error opening input files: Server returned 404 Not Found
            traceback.print_exc()


def load_live_event():
    event_api_url = load_config('streamPlayFeed')
    response = requests.get(f'{event_api_url}?start=today&end=tomorrow&pageSize=1')
    response.raise_for_status()
    response = response.json()
    assert len(response['eventData']) == 1, 'Unable to load event data.'
    return response['eventData'][0]


def load_event(media_id: str):
    api_url = load_config('cloudMatrixAPI')
    response = requests.get(f'{api_url}/en/search?query=(mediaData.entryId:{media_id})')
    response.raise_for_status()
    response = response.json()
    assert len(response['itemData']) == 1, 'Unable to load event data.'
    return response['itemData'][0]


def prepare_session(media_id: str):
    id_token, access_token, refresh_token = extract_cookies_from_browser()
    assert id_token is not None, 'Unable to extract cognito tokens from cookies.'

    user_agent_original = load_user_agent()
    user_agent = user_agent_original
    assert user_agent is not None, 'Unable to load user agent.'
    user_agent_adjustment_original = 5
    user_agent_adjustment = user_agent_adjustment_original

    api_key = load_config('webVideoOnDemandUiConf')
    assert api_key is not None, 'Unable to load API key.'

    while True:
        try:

            headers = {
                'authorization': f'Bearer {id_token}',
                'user-agent': user_agent,
                'x-api-key': api_key,
            }

            refresh_sso(id_token)

            response = requests.get(f'https://api.playback.streamamg.com/v1/entry/{media_id}', headers=headers)
            response.raise_for_status()
            return response.json(), headers

        except RequestException as e:
            if e.response.status_code == 401:
                error = e.response.json()
                if error['reason'] == 'BAD_REQUEST_ERROR' and error['message'] == 'jwt expired':
                    claims = jwt.get_unverified_claims(id_token)
                    assert int(claims['exp']) < timegm(datetime.now(tz=timezone.utc).utctimetuple()), 'Token has not expired, but is still invalid.'
                    id_token, access_token, refresh_token = refresh_tokens(id_token, refresh_token)
                    continue
                if error['reason'] == 'TOO_MANY_DEVICES':  # may error if chrome is in middle of updating
                    user_agent = adjust_user_agent_version(user_agent_original, user_agent_adjustment) \
                        if user_agent_adjustment >= -user_agent_adjustment_original else None  # only check x above and x below
                    assert user_agent is not None, 'Fatal: TOO_MANY_DEVICES (possible blackout policy in your market)'
                    user_agent_adjustment -= 1
                    continue
            pprint('\n\n', e.request, '\n\n', e.response, '\n\n')
            raise

    assert False, 'Unable to load metadata.'


def extract_cookies_from_browser():
    id_token = None
    access_token = None
    refresh_token = None

    cookies: YoutubeDLCookieJar = yt_dlp.cookies.extract_cookies_from_browser('chrome', profile='default')
    for cookie in cookies:
        if cookie.domain == '.wrexhamafc.co.uk' and cookie.name.startswith('CognitoIdentityServiceProvider.'):
            if cookie.name.endswith('.idToken'):
                id_token = cookie.value
            elif cookie.name.endswith('.accessToken'):
                access_token = cookie.value
            elif cookie.name.endswith('.refreshToken'):
                refresh_token = cookie.value
            if id_token and access_token and refresh_token:
                break

    return id_token, access_token, refresh_token


def load_user_agent():
    driver = None
    try:
        options = Options()
        options.add_argument('--headless')
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        user_agent = driver.execute_script('return navigator.userAgent;')
        user_agent = user_agent.replace('HeadlessChrome', 'Chrome')
        return user_agent
    finally:
        if driver: driver.quit()


def adjust_user_agent_version(user_agent: str, offset: int):
    match = re.search(r'(Chrome/(\d+)\.0\.0\.0)', user_agent)
    if not (match and match.group(1) and match.group(2)): return None
    if not (int(match.group(2)) > 101): return None
    return user_agent.replace(match.group(1), f'Chrome/{int(match.group(2)) + offset}.0.0.0')


def load_config(key: str):
    response = requests.get('https://www.wrexhamafc.co.uk/live')
    response.raise_for_status()
    match = re.search(fr'[{{\s,]?{key}\s*:\s*"([^"]+)"', response.text)
    return match.group(1) if match else None


def refresh_sso(id_token: str):
    claims = jwt.get_unverified_claims(id_token)

    try:

        requests.get(
            url=f"https://sso.cms.web.gc.wrexhamafcservices.co.uk/v2/{claims['sub']}",
            params={'token': id_token}
        ).raise_for_status()

        requests.get(
            url=f'https://wrexhampayments.streamamg.com/sso/start',
            params={'token': id_token}
        ).raise_for_status()

    except RequestException as e:
        if e.response.status_code not in (401, 403):
            pprint('\n\n', e.request, '\n\n', e.response, '\n\n')
        raise


def refresh_tokens(id_token: str, refresh_token: str):
    claims = jwt.get_unverified_claims(id_token)

    user_pool_id = claims['iss'].rsplit('/', maxsplit=1)[1]
    user_pool_region = user_pool_id.split('_', maxsplit=1)[0]

    client = boto3.client('cognito-idp', region_name=user_pool_region)

    response = client.initiate_auth(
        ClientId=claims['aud'],
        AuthFlow='REFRESH_TOKEN_AUTH',
        AuthParameters={'REFRESH_TOKEN': refresh_token},
    )

    return (
        response['AuthenticationResult']['IdToken'],
        response['AuthenticationResult']['AccessToken'],
        response['AuthenticationResult']['RefreshToken']
    )


def pprint(*args):
    for arg in args:
        if arg is None:
            continue
        elif isinstance(arg, str):
            print(arg)
        elif isinstance(arg, PreparedRequest):
            print(f'{arg.method} {arg.url}')
            print('\n'.join('{}: {}'.format(k, v) for k, v in arg.headers.items()))
            if arg.body: print(f'\n{arg.body.decode("utf-8") if isinstance(arg.body, bytes) else arg.body}')
        elif isinstance(arg, Response):
            print(f'HTTP/1.1 {arg.status_code} {http.client.responses[arg.status_code]}')
            print('\n'.join('{}: {}'.format(k, v) for k, v in arg.headers.items()))
            try:
                print(f'\n{arg.content.decode("utf-8") if isinstance(arg.content, bytes) else arg.content}')
            except UnicodeDecodeError:
                print(f'\n<binary>')
            finally:
                pass
        else:
            print(repr(arg))


if __name__ == '__main__':
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('--id', dest='media_id', type=str)
    args_parser.add_argument('--type', dest='media_type', type=str)
    args = args_parser.parse_args()

    try:
        download(
            media_id=args.media_id or 'live',
            media_type=args.media_type or 'video',
            ytdlp_options=dict(  # see yt_dlp.YoutubeDL for available options
                verbose=False,
                quiet=False,
                no_warnings=False,
            )
        )
    except AssertionError as e:
        print(str(e))
