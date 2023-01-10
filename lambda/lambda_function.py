import boto3
from botocore import awsrequest
from botocore import crt

print(boto3.__version__)

failover_header = 'originTypeFailover'
cf_read_only_headers_list = [h.lower() for h in [
    'Accept-Encoding',
    'Content-Length',
    'If-Modified-Since',
    'If-None-Match',
    'If-Range',
    'If-Unmodified-Since',
    'Transfer-Encoding',
    'Via'
]]


class SigV4AWrapper:

    def __init__(self):
        self._session = boto3.Session()

    def get_auth_headers(self, method, endpoint, data, region, service, headers):
        sigv4a = crt.auth.CrtS3SigV4AsymAuth(self._session.get_credentials(), service, region)
        request = awsrequest.AWSRequest(method=method, url=endpoint, data=data, headers=headers)
        sigv4a.add_auth(request)
        prepped = request.prepare()
        return prepped.headers


def lambda_handler(event, context):
    request = event['Records'][0]['cf']['request']

    origin_key = list(request['origin'].keys())[0]
    custom_headers = request['origin'][origin_key].get('customHeaders', {})

    # Check failover case. If CloudFront origin customer header is included that signals it's the failover request.
    # In this case, assumed, SigV4A singing should not be performed and
    # unmodified request should be used for the failover origin.
    if failover_header in custom_headers:
        return request

    method = request["method"]
    endpoint = f"https://{request['origin']['custom']['domainName']}{request['uri']}"
    data = None  # Empty for GET, could be mapped from request, if there is such case. E.g. request['body']['data']
    region = '*'  # For S3 Multi-Region Access Point it's '*' (e.g. all regions). Also, that's why SigV4A is required.
    service = 's3'

    headers = request["headers"]
    request_headers_list = list(headers.keys())

    cf_read_only_headers = {}
    # Some CloudFront headers are read-only and can't be removed from the request.
    # Therefore those have to be part of signing headers. See more details in docs
    # https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-functions-restrictions.html
    for h in cf_read_only_headers_list:
        if h in request_headers_list:
            cf_read_only_headers[headers[h][0]['key']] = headers[h][0]['value']

    # CloudFront adds "X-Amz-Cf-Id" header after Origin request Lambda but before the request to the origin.
    # Therefore it has to be part of the signing request.
    cf_read_only_headers['X-Amz-Cf-Id'] = event['Records'][0]['cf']['config']['requestId']

    # Sign the request with Signature Version 4A (SigV4A).
    auth_headers = SigV4AWrapper().get_auth_headers(method, endpoint, data, region, service, cf_read_only_headers)

    # "X-Amz-Cf-Id" header can't be directly set in request object.
    # Therefore it has to be part of the signing request, however has to be removed from the
    # request object as CloudFront will set it before making request to the origin.
    auth_headers.pop('X-Amz-Cf-Id')

    cf_headers = {}
    # Add SigV4A auth headers in the by CloutFront expected data structure.
    for k, v, in auth_headers.items():
        cf_headers[k.lower()] = [{'key': k, 'value': v}]

    # Override headers to only include the one expected by S3 Multi-Region Access Point (e.g. the one that are signed).
    request['headers'] = cf_headers

    # If querystring is in request, remove as else signature won't match.
    # Note: You can have querystring be part of the request, however you first need to add those in the signing request.
    request.pop('querystring')

    return request
