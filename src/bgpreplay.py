#!/usr/bin/env python
import atexit
import sys
import os
import re
import subprocess
import tempfile
import random
import time
import json
import traceback
import ipaddress
import socket
import requests
import logging
from requests.auth import HTTPBasicAuth
from oslo_config import cfg

logger = logging.getLogger('bgpreplay')
logger.setLevel(logging.INFO)

WELLKNOWN_DEFAULTS = {
    'as_path': [1],
    'origin': 2,
    'nexthop': '127.0.0.1',
}

class ConsoleAgent(object):
    """Print BGP messages to stdout."""
    def start(self, *args):
        return

    def stop(self):
        return

    def connected(self, timeout=120):
        return True

    def send_update(self, update):
        logger.info(str(update))
        print(update)


class ExaBGPAgent(object):
    """This tells us to use ExaBGP as BGP library to connect to BGP routers and send out updates."""
    exabgp = None
    config_file = None
    socket = None

    def start(self, peers, local_ip, local_as):
        """Start ExaBGP in subprocess."""
        CONFIG = """
process announce {
    run nc -l -U %s;
    encoder json;
}
        """
        PEER_CONFIG = """
neighbor %s {
    passive;
    connect %s;
    peer-as %s;
    local-address %s;
    local-as %s;
    router-id 192.168.192.192;
    api {
        processes [announce];
    }
}
        """
        _, self.config_file = tempfile.mkstemp()
        _, self.logfile = tempfile.mkstemp()
        self.sock_path = os.path.join(tempfile.gettempdir(), 'bgpreplay.sock')
        print('exabgp log is located at: %s' % self.logfile)
        with open(self.config_file, 'w') as f:
            config = CONFIG % self.sock_path
            f.write('%s\n' % config)
            for peer in peers:
                peer_ip, peer_port, peer_as = peer
                peer_config = PEER_CONFIG % (peer_ip, peer_port, peer_as, local_ip, local_as)
                f.write('%s\n' % peer_config)
        self.exabgp = subprocess.Popen(
            ['env', 'exabgp.tcp.bind=' + '0.0.0.0', 'exabgp.tcp.port=' + '179',
             'exabgp.daemon.daemonize=false', 'exabgp.daemon.user=root',
             'exabgp.log.level=INFO',
             'exabgp.log.all=true',
             'exabgp.log.destination=%s' % self.logfile,
             'exabgp', self.config_file],
             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.peers = peers
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        success = False
        for _ in range(10):
            try:
                self.socket.connect(sock_path)
                success = True
                break
            except:
                time.sleep(1)
        if not success:
            print('ExaBGPAgent failed to start')
            sys.exit(-1)

    def stop(self):
        """stop Exabgp running in the subprocess."""
        print('stopping...')
        if self.socket:
            self.socket.close()
        if self.exabgp:
            self.exabgp.kill()
        if self.config_file:
            os.remove(self.config_file)
        if self.sock_path:
            os.remove(self.sock_path)

    def connected(self, timeout=60):
        """wait for connection to BGP peers."""
        with open(self.logfile) as f:
            for _ in range(timeout):
                log = f.read()
                for peer_ip, _, _ in self.peers: # must have at least one connected peer
                    if re.search('connected.*%s' % peer_ip, log):
                        return True
                time.sleep(1)
        return False

    def _to_exabgp_format(self, update):
        exabgp_attr_name_conversion = {
                'local_pref': 'local-preference', 'nexthop': 'next-hop', 'as_path': 'as-path',
                'med': 'med', 'origin': 'origin',
                }
        statements = []
        peers = update.get('peers', [])
        if peers:
            announce_template = 'neighbor {neighbor} announce attributes {attr} nlri {nlri}'
            withdraw_template = 'neihgbor {neighbor} withdraw {withdraw}'
        else:
            announce_template = 'announce attributes {attr} nlri {nlri}'
            withdraw_template = 'withdraw route {withdraw}'
        nlri = update.get('nlri', [])
        if nlri:
            attr = ''
            for at_name, at_value in update['attr'].items():
                if at_name in exabgp_attr_name_conversion:
                    attr += ' %s %s' % (exabgp_attr_name_conversion[at_name], str(at_value))
                else:
                    print(at_name, at_value)
            nlri = ' '.join(nlri)
            for peer in peers:
                statements.append(announce_template.format(neighbor=peer, attr=attr, nlri=nlri))
            if not peers:
                statements.append(announce_template.format(attr=attr, nlri=nlri))
        withdraw = update.get('withdraw', [])
        if withdraw:
            withdraw = ' '.join(withdraw)
            for peer in peers:
                statements.append(withdraw_template.format(neighbor=peer, withdraw=withdraw))
            if not peers:
                statements.append(withdraw_template.format(withdraw=withdraw))
        return statements

    def send_update(self, update):
        """send the update to client process in ExaBGP."""
        if self.socket:
            logger.info(str(update))
            for statement in self._to_exabgp_format(update):
                statement += '\r\n'
                self.socket.sendall(statement.encode('utf-8'))


class YaBGPAgent(object):
    """
    """
    yabgp = None
    yabgp_port = 5555
    yabgp_url = 'http://localhost:%d/v1' % yabgp_port
    def start(self, peers, local_ip, local_as):
        peer_ip, peer_port, peer_as = peers[0]
        self.yabgp = subprocess.Popen([
            'yabgpd',
            '--bgp-local_as', str(local_as),
            '--bgp-remote_as', str(peer_as),
            '--bgp-remote_addr', str(peer_ip),
            '--bgp-remote_port', str(peer_port),
            '--rest-bind_host', '127.0.0.1',
            '--rest-bind_port', str(self.yabgp_port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        time.sleep(5)
        self.session = requests.Session()
        self.peer = None
        self.headers = {'content-type':'application/json'}

    def stop(self):
        if self.yabgp:
            self.yabgp.kill()

    def _get(self, path):
        data = self.session.get(self.yabgp_url + '/' + path, auth=HTTPBasicAuth('admin', 'admin'))
        return data.json()

    def connected(self):
        for _ in range(60):
            data = self._get('peers')
            for peer in data.get('peers', []):
                if peer['fsm'] == 'ESTABLISHED':
                    self.peer = peer
                    return True
        return

    def _build_yabgp_msgs(self, update):
        yabgp_attr_name_conversion = {
            'nexthop': 3, 'origin': 1, 'as_path': 2, 'local_pref': 5 }
        yabgp_msg = {}
        if not self.peer:
            return {}
        nlri = update.get('nlri')
        if nlri:
            attributes = {}
            attr = update['attr']
            for at_name, at_value in attr.items():
                if at_name == 'as_path':
                    at_value = [[2, at_value]]
                if at_name in yabgp_attr_name_conversion:
                    if at_name == 'origin':
                        if at_value == 'igp':
                            at_value = 0
                        elif at_value == 'egp':
                            at_value = 1
                        else:
                            at_value = 2
                    attributes[yabgp_attr_name_conversion[at_name]] = at_value
            yabgp_msg['attr'] = attributes
            yabgp_msg['nlri'] = nlri
        withdraw = update.get('withdraw')
        if withdraw:
            yabgp_msg['withdraw'] = withdraw
        return {self.peer['remote_addr']: yabgp_msg}

    def _send_yabgp(self, update):
        yabgp_msg = self._build_yabgp_msgs(update)
        for peer_ip, msg in yabgp_msg.items():
            if not msg:
                continue
            res = self.session.post(
                    '%s/peer/%s/send/update' % (self.yabgp_url, peer_ip),
                    data=json.dumps(msg), auth=HTTPBasicAuth('admin', 'admin'),
                    headers=self.headers)
            print(res)

    def send_update(self, update):
        # verify if well-known, mandatory attrbiutes exist
        for attr, value in WELLKNOWN_DEFAULTS.items():
            if attr not in update['attr']:
                update['attr'][attr] = value
        if self.peer:
            logger.info(str(update))
            self._send_yabgp(update)


BGP_AGENTS  = {
        'console': ConsoleAgent,
        'yabgp': YaBGPAgent,
        'exabgp': ExaBGPAgent
        }

class BgpUpdateGenerator(object):
    """Generate random BGP updates, replay from a MRT file or live from a CAIDA collector.
    """

    def __init__(self, config):
        self.config = config
        self.agent = BGP_AGENTS[config['agent']]()

    def run(self):
        """Start sending updates."""
        try:
            self.agent.start(self.config['peers'], self.config['local_ip'], self.config['local_as'])
            if not self.agent.connected():
                print('no BGP router is connected')
                return
            time.sleep(1)
            if self.config['mrt']:
                self._send_update_from_source(source_type='mrt_file', filename=self.config['mrt'])
            elif self.config['live']:
                self._send_update_from_source(source_type='live', collector=self.config['live'])
            elif self.config['text']:
                self._send_update_from_text_file(self.config['text'], self.config['count'])
            else:
                self._send_random_update()
            self.agent.stop()
        except (KeyboardInterrupt, Exception):
            self.agent.stop()
            traceback.print_exc()

    def _random_nexthop(self):
        if not self.config['nexthop']:
            return None
        return str(random.choice(self.config['nexthop']))

    def _send_update_from_text_file(self, fname, count=0):
        """
        time different from the previous
        delay(ms),update,prefix,nexthop,local_pref,as_path
        0,annouce,1.0.0.0/24,10.0.0.1,None,1 2 3
        10,withdraw,2.0.0.0/24,10.0.0.1,120,1 2 3
        """
        sent = 0
        with open(fname, 'r') as f:
            for line in f:
                try:
                    if count and sent == count:
                        break
                    delay, update, prefix, nexthop, localpref, aspath = line.split(',')
                    delay = float(delay)
                    prefix = str(ipaddress.ip_network(prefix))
                    if update == 'announce':
                        nexthop = str(ipaddress.ip_address(nexthop))
                        localpref = int(localpref)
                        aspath = list(map(int, aspath.split(' ')))
                        nlri = [ prefix ]
                        withdraw = []
                    else:
                        nexthop = None
                        localpref = None
                        aspath = []
                        nlri = []
                        withdraw = [ prefix ]
                    attr = {
                            'nexthop': nexthop,
                            'as_path': aspath,
                            'local_pref': localpref}
                    update = {
                            'attr': attr,
                            'nlri': nlri,
                            'withdraw': withdraw,
                            }
                    time.sleep(delay/1000)
                    self.agent.send_update(update)
                    sent += 1
                except Exception as e:
                    print(e)


    def _send_random_update(self):
        """generate updates randomly."""
        def random_prefix():
            prefix = ".".join(map(str, (random.randint(0,255) for _ in range(3))))
            prefix += '.0/24'
            return prefix

        def random_prefixes(max_prefix):
            prefixes = []
            for _ in range(random.randint(1, max_prefix)):
                prefixes.append(random_prefix())
            return prefixes

        def random_as_path(max_length=5):
            as_path = [self.config['local_as']]
            for _ in range(random.randint(0, max_length)):
                as_path.append(random.randint(1, 64999))
            return as_path

        def sample(seq, num):
            if len(seq) >= num:
                return random.sample(seq, num)
            else:
                return list(seq)
        sent = 0
        update_per_sec = self.config['rate'] or 1
        update_per_sec = float(update_per_sec)
        announced_prefixes = set()
        rate = self.config['rate']
        while sent < self.config['count'] or self.config['count'] == 0:
            update = {
                'attr': {
                        'nexthop': self._random_nexthop(),
                        'med': random.randint(0, 100),
                        'origin': random.choice(['igp', 'incomplete', 'egp']),
                        'as_path': random_as_path(),
                        'local_pref': random.randint(100, 150),
                        }
                }
            update['nlri'] = []
            update['withdraw'] = []
            if self.config['update_type'] == 'announce':
                update['nlri'] = random_prefixes(self.config['max_prefix'])
                announced_prefixes.update(update['nlri'])
            elif self.config['update_type'] == 'withdraw':
                update['withdraw'] = random_prefixes(self.config['max_prefix'])
            else:
                if random.getrandbits(1):
                    update['withdraw'] = sample(announced_prefixes, self.config['max_prefix'])
                if random.getrandbits(1):
                    update['nlri'] = random_prefixes(self.config['max_prefix'])
                    announced_prefixes.update(update['nlri'])
            self.agent.send_update(update)
            sent += 1
            time.sleep(1/update_per_sec)

    def _send_update_from_source(self, source_type, **kwargs):
        stream = None
        if source_type == 'mrt_file':
            from .pybgpdump import BGPDump
            stream = BGPDump(kwargs['filename'])
        elif source_type == 'live':
            from bgpstream import BGPStreamReader
            stream = BGPStreamReader({'collector': kwargs['collector']})
        else:
            print('unsupported type: %s' % source_type)
            sys.exit(-1)

        delay = 0.0
        prev_timestamp = 0
        sent = 0
        self.update_per_sec = self.config['rate']
        while sent < self.config['count'] or self.config['count'] == 0:
            try:
                timestamp, attr, nlri, withdraw = stream.next()
                attr['nexthop'] = self._random_nexthop()
                update = {
                        'attr': attr,
                        'nlri': nlri,
                        'withdraw': withdraw,
                        }
                self.agent.send_update(update)
                sent += 1
                if prev_timestamp == 0:
                    delay = 0.0
                else:
                    delay = timestamp - prev_timestamp
                prev_timestamp = timestamp
                if self.update_per_sec:
                    time.sleep(1/self.update_per_sec)
                else:
                    time.sleep(delay)
            except Exception as e:
                traceback.print_exc()
                sys.exit(-1)

    def cleanup(self):
        self.agent.stop()

def setup_cli_opts():
    CONF = cfg.CONF
    cli_opts = [
        cfg.MultiStrOpt('peers', short='p',
            help='one or more peers to send update to. It takes format address:port/asn, ex: 127.0.0.1:179/65000'),
        cfg.StrOpt('mrt', help='BGP MRT file to replay'),
        cfg.StrOpt('text', help='Generate BGP updates from a text file'),
        cfg.StrOpt('live', help='Replay BGP updates from live feed (a valid CAIDA collector, ex:rrc00)'),
        cfg.BoolOpt('rand', help='Randomly generate BGP updates. It is enabled by default if file or live is not specified'),
        cfg.StrOpt('agent', short='a',
            choices=[('yabgp', 'https://github.com/smartbgp/yabgp'),
                     ('exabgp', 'https://github.com/Exa-Networks/exabgp'),
                     ('console', 'Print to screen')],
            help='Use YaBGP or ExaBGP for BGP peering or simply print to screen'),
        cfg.IntOpt('count', short='c',
            help='Number of updates to send. Use 0 for no limit (default)'),
        cfg.FloatOpt('rate', short='r',
            help='Number of updates per sec, if not specified, based on timestamp in MRT file, or 1 for random updates'),
        cfg.IntOpt('max_prefix', short='m',
            help='Max number of prefixes per updates. Default=1. The actual number is randomly between 1 to the max'),
        cfg.StrOpt('update_type', short='t', choices=['announce', 'withdraw', 'mixed'],
            help='Type of updates: announce, withdraw or mixed (default)'),
        cfg.MultiStrOpt('nexthop', short='nh',
            help='A nexthop(s) to use for announcements. Default=IP address used to establish the peering'),
        cfg.IntOpt('local_as', help='Local ASN, default=65000'),
        cfg.StrOpt('local_ip', help='Local IP, default=127.0.0.1'),
        cfg.StrOpt('logfile', help='Log file'),
    ]
    CONF.register_cli_opts(cli_opts)
    return CONF

DEFAULTS = {
        'live': None,
        'text': None,
        'mrt': None,
        'rand': True,
        'peers': ['127.0.0.1:9179/65000'],
        'agent': 'console',
        'count': 0,
        'rate': 0,
        'max_prefix': 1,
        'update_type': 'mixed',
        'nexthop': ['127.0.0.1'],
        'local_as': 65000,
        'local_ip': '127.0.0.1',
        }

def check_peer_format(peers):
    results = []
    try:
        if type(peers) == str:
            peers = peers.split(' ')
        for peer in peers:
            if not peer:
                continue
            ip, port, asn = re.split(':|/', peer)
            try:
                ip = str(ipaddress.ip_address(ip))
            except:
                ip = socket.gethostbyname(ip)
            results.append((ip, port, asn))
    except Exception as e:
        print('Incorrect argument format: %s. Error: %s' % (peers, e))
        sys.exit(-1)
    return results

def check_nexthop_format(nexthops):
    results = []
    try:
        if type(nexthops) == str:
            nexthops = nexthops.split(' ')
        for nexthop in nexthops:
            if not nexthop:
                return
            results.append(ipaddress.ip_address(u'%s' % nexthop))
    except Exception as e:
        print('Incorrect argument format: %s. Error: %s' % (nexthops, e))
        sys.exit(-1)
    return results


CHECKS = {
        'peers': check_peer_format,
        'nexthop': check_nexthop_format,
        'local_as': int,
        }

def main():
    conf = setup_cli_opts()
    conf(args=sys.argv[1:])

    config = {}
    for param in DEFAULTS.keys():
        value = getattr(conf, param) or os.environ.get('BGPREPLAY_' + param.upper(), None) or DEFAULTS[param]
        if param in CHECKS:
            value = CHECKS[param](value)
        config[param] = value
    print(config)
    logfile = getattr(conf, 'logfile') or os.environ.get('BGPREPLAY_LOGFILE', None)
    if logfile:
        print('The generated BGP updates are being recorded to: %s' % logfile)
        hdl = logging.FileHandler(logfile)
        hdl.setLevel(logging.INFO)
        hdl.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
        logger.addHandler(hdl)

    bgpgen = BgpUpdateGenerator(config)
    atexit.register(bgpgen.cleanup)
    bgpgen.run()

if __name__ == '__main__':
    main()
