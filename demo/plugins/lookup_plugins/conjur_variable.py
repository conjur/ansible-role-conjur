#!/usr/bin/python

import os.path
import ssl
import yaml
from ansible.plugins.lookup import LookupBase
from base64 import b64encode
from httplib import HTTPConnection
from netrc import netrc
from os import environ
from sys import stderr
from time import time
from urllib import quote_plus
from urlparse import urlparse


def exit_error(err):
    stderr.write(err)
    exit(1)


class Token:
    def __init__(self, http_connection, id, api_key, account):
        self.http_connection = http_connection
        self.id = id
        self.api_key = api_key
        self.token = None
        self.refresh_time = 0
        self.account = account

    # refresh
    # Exchanges API key for an auth token, storing it base64 encoded within the
    # 'token' member variable. If it fails to obtain a token, the process exits.
    def refresh(self):
        authn_url = '/authn/{}/{}/authenticate'.format(quote_plus(self.account), quote_plus(self.id))
        self.http_connection.request('POST', authn_url, self.api_key)

        response = self.http_connection.getresponse()

        if response.status != 200:
            exit_error('Failed to authenticate as \'{}\''.format(self.id))

        self.token = b64encode(response.read())
        self.refresh_time = time() + 5 * 60

    # get_header_value
    # Returns the value for the Authorization header. Refreshes the auth token
    # before returning if necessary.
    def get_header_value(self):
        if time() >= self.refresh_time:
            self.refresh()

        return 'Token token="{}"'.format(self.token)


def load_conf(conf_path):
    conf_path = os.path.expanduser(conf_path)

    # if the conf is not in the path specified, of if an exception is thrown while reading the conf file
    # we don't exit as the conf might be in another path
    if not os.path.isfile(conf_path):
        return {}

    with open(conf_path, 'r') as conf_file:
        try:
            return yaml.load(conf_file)
        except yaml.YAMLError as e:
            exit_error(e)


def load_identity(identity_path, appliance_url):
    identity_path = os.path.expanduser(identity_path)

    # if the identity is not in the path specified, of if an exception is thrown while reading the identity file
    # we don't exit as the identity might be in another path
    if not os.path.isfile(identity_path):
        return {}

    try:
        identity = netrc(identity_path)
        id, _, api_key = identity.authenticators('{}/authn'.format(appliance_url))
        if not id or not api_key:
            return {}

        return {"id": id, "api_key": api_key}
    except:
        pass

    return {}


# Merges all key values in a variable list of dictionaries
def merge_dict(*arg):
    ret = {}
    for a in arg:
        ret.update(a)
    return ret


class LookupModule(LookupBase):
    def run(self, terms, variables, **kwargs):

        # Load Conjur configuration
        # todo - is it ok to have the identity in more than one place? Do we want to change this?
        conf = merge_dict(load_conf('/etc/conjur.conf'),
                          # load_conf('~/.conjurrc'),
                          {
                              "account": environ.get('CONJUR_ACCOUNT'),
                              "appliance_url": environ.get("CONJUR_APPLIANCE_URL"),
                              "cert_file": environ.get('CONJUR_CERT_FILE')
                            })

        if not conf:
            exit_error('Conjur configuration should be in environment variables or in one of the following paths: \'~/.conjurrc\', \'/etc/conjur.conf\'')

        # Load Conjur identity
        # todo - is it ok to have the conf in more than one place? Do we want to change this?
        identity = merge_dict(
            load_identity('/etc/conjur.identity', conf['appliance_url']),
            # load_identity('~/.netrc', conf['appliance_url']),
            {
                "api_key": environ.get('CONJUR_AUTHN_API_KEY'),
                "id": environ.get('CONJUR_AUTHN_LOGIN')
            })

        if not identity:
            exit_error('Conjur identity should be in environment variables or in one of the following paths: \'~/.netrc\', \'/etc/conjur.identity\'')

        # Load our certificate for validation
        # ssl_context = ssl.create_default_context()
        # ssl_context.load_verify_locations(conf['cert_file'])

        conjur_https = HTTPConnection(urlparse(conf['appliance_url']).netloc)
        # todo orenbm: change to https

        token = Token(conjur_https, identity['id'], identity['api_key'], conf['account'])

        # retrieve secrets of the given variables from Conjur
        secrets = []
        for term in terms:
            variable_name = term.split()[0]
            headers = {'Authorization': token.get_header_value()}
            url = '/secrets/{}/variable/{}'.format(conf['account'], quote_plus(variable_name))

            conjur_https.request('GET', url, headers=headers)
            response = conjur_https.getresponse()

            if response.status == 200:
                secrets.append(response.read())

        return secrets
