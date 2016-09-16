# Copyright 2016 Dravetech AB. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

"""
Napalm driver for VyOS.

Read napalm.readthedocs.org for more information.
"""

import re

# third party libs
import paramiko
import vyattaconfparser

# NAPALM base
from napalm_base.base import NetworkDriver
from napalm_base.exceptions import ConnectionException, SessionLockedException, \
                                   MergeConfigException, ReplaceConfigException,\
                                   CommandErrorException


class VyOSDriver(NetworkDriver):

  _CMD_PREFIX = ". /etc/bash_completion; eval _vyatta_op_run "
  _MINUTE_SECONDS = 60 
  _HOUR_SECONDS =  60 * _MINUTE_SECONDS
  _DAY_SECONDS  =  24 * _HOUR_SECONDS
  _WEEK_SECONDS =   7 * _DAY_SECONDS
  _YEAR_SECONDS = 365 * _DAY_SECONDS

  def __init__(self, hostname, username, password, timeout=60, optional_args=None):
    self._hostname = hostname
    self._username = username
    self._password = password
    self._timeout  = timeout    
    self._device  = None
    if optional_args and optional_args.has_key('port'):
        self._port = optional_args['port']
    else:
        self._port = 22
    self._new_config = None
    self._old_config = None


  def open(self):
    self._device = paramiko.SSHClient()
    self._device.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    self._device.connect(self._hostname, self._port, self._username, self._password)


  def close(self):
    self._device.close()


  def load_merge_candidate(self, filename=None, config=None):
    if filename is not None:
      if os.path.exists(filename) == True:
        with open(filename) as f:
          self._new_config = f.read()
      else:
        raise MergeConfigException("config file is not found")
    elif config is not None:
      self._new_config = config
    else:
      raise MergeConfigException("no configuration found")


  def discard_config(self):
    self._new_config = None 


  def commit_config(self):
    """
    This method uses "vyatta-cfg-cmd-wrapper" script. 
    If you want to set the eth0's description "Management", you can do it with the following commands:
      /opt/vyatta/sbin/vyatta-cfg-cmd-wrapper begin;
      /opt/vyatta/sbin/vyatta-cfg-cmd-wrapper set interfaces ethernet eth0 description Management;
      /opt/vyatta/sbin/vyatta-cfg-cmd-wrapper commit;
      /opt/vyatta/sbin/vyatta-cfg-cmd-wrapper end;
    """
    cfg_cmd_wrapper = "/opt/vyatta/sbin/vyatta-cfg-cmd-wrapper "
    # delete empty element
    cfg = [x for x in self._new_config.split("\n") if x is not ""]
    
    # arrangemenet config commands 
    cfg_cmd = [cfg_cmd_wrapper + x + ";" for x in cfg]
    cfg_cmd = cfg_cmd_wrapper + "begin;" \
            + ''.join(cfg_cmd) \
            + cfg_cmd_wrapper + "commit;" \
            + cfg_cmd_wrapper + "end;"

    self._send_command(cfg_cmd)


  def get_environment(self):
    # 'fans', 'temperature' and 'power' is not implemented yet

    """
    'vmstat' output:
    procs -----------memory---------- ---swap-- -----io---- -system-- ----cpu----
    r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa
    0  0      0  61404 139624 139360    0    0     0     0    9   14  0  0 100  0
    """
    output_cpu = self._send_command("vmstat", is_vyatta_op=False).split("\n")[-2]
    cpu = 100 - int(output_cpu.split()[-2])

    """
    'free' output:
                 total       used       free     shared    buffers     cached
    Mem:        508156     446784      61372          0     139624     139360
    -/+ buffers/cache:     167800     340356
    Swap:            0          0          0
    """
    output_ram = self._send_command("free", is_vyatta_op=False).split("\n")[1]
    available_ram, used_ram = output_ram.split()[1:3]

    environment = {
      "fans": {
        "status": None
      },
      "temperature": {
        "temperature": None,
        "is_alert"   : None,
        "is_critical": None
      },
      "power": {
        "status"  : None,
        "capacity": None,
        "output"  : None
      },
      "cpu": {
        "0": {
          "%usage": cpu
        },
      },
      "memory": {
        "available_ram": int(available_ram),
        "used_ram"     : int(used_ram)
      }
    }

    return environment


  def get_interfaces(self):
    """
    "show interfaces" output example:
    Interface        IP Address                        S/L  Description
    ---------        ----------                        ---  -----------
    br0              -                                 u/D  
    eth0             192.168.1.1/24                   u/u  Management 
    eth1             192.168.1.2/24                    u/u  
    eth2             192.168.3.1/24                    u/u  foobar
                     192.168.2.2/24
    lo               127.0.0.1/8                       u/u
                     ::1/128
    """
    output_iface = self._send_command("show interfaces")

    # Collect all interfaces' name and status
    match = re.findall("(\S+)\s+[:\-\d/\.]+\s+([uAD])/([uAD])", output_iface)

    # 'match' example:
    # [("br0", "u", "D"), ("eth0", "u", "u"), ("eth1", "u", "u")...]
    iface_state = {iface_name:{"State": state, "Link": link} for iface_name, state, link in match}

    output_conf = self._send_command("show configuration")

    # Convert the configuration to dictionary 
    config = vyattaconfparser.parse_conf(output_conf)

    iface_dict = dict()

    for iface_type in config["interfaces"]: 

      ifaces_detail = config["interfaces"][iface_type]

      for iface_name in ifaces_detail:

        description = self._get_value("description", ifaces_detail[iface_name])
        speed = self._get_value("speed", ifaces_detail[iface_name])
        hw_id = self._get_value("hw-id", ifaces_detail[iface_name])

        is_up      = (iface_state[iface_name]["Link"]  == "u")
        is_enabled = (iface_state[iface_name]["State"] == "u")

        iface_dict.update({
          iface_name: {
            "is_up"        : is_up,
            "is_enabled"   : is_enabled,
            "description"  : description,
            "last_flapped" : -1,
            "speed"        : speed,
            "mac_address"  : hw_id 
          }
        })

    return iface_dict


  # for avoiding KeyError
  @staticmethod
  def _get_value(key, target_dict):
    if key in target_dict:
      return target_dict[key]
    else:
      return None


  def get_arp_table(self):
    # 'age' is not implemented yet

    """
    'show arp' output example:
    Address                  HWtype  HWaddress           Flags Mask            Iface
    10.129.2.254             ether   00:50:56:97:af:b1   C                     eth0
    192.168.1.134                    (incomplete)                              eth1
    192.168.1.1              ether   00:50:56:ba:26:7f   C                     eth1
    10.129.2.97              ether   00:50:56:9f:64:09   C                     eth0
    192.168.1.3              ether   00:50:56:86:7b:06   C                     eth1
    """
    output = self._send_command("show arp")
    output = output.split("\n")

    # Skip the header line
    output = output[1:-1]

    arp_table = list() 

    for line in output:

      line = line.split()

      # 'line' example:
      # ["10.129.2.254", "ether", "00:50:56:97:af:b1", "C", "eth0"]
      arp_table.append({
        "interface" : unicode(line[4]),
        "mac" : unicode(line[2]),
        "ip"  : unicode(line[0]),
        "age" : None
      })

    return arp_table


  def get_ntp_stats(self):
    """
    'ntpq -np' output example 
         remote           refid      st t when poll reach   delay   offset  jitter
    ==============================================================================
     116.91.118.97   133.243.238.244  2 u   51   64  377    5.436  987971. 1694.82
     219.117.210.137 .GPS.            1 u   17   64  377   17.586  988068. 1652.00
     133.130.120.204 133.243.238.164  2 u   46   64  377    7.717  987996. 1669.77
    """

    output = self._send_command("ntpq -np", is_vyatta_op=False).split("\n")[2:-1]

    ntp_stats = list()

    for ntp_info in output:
      remote, refid, st, t, when, hostpoll, reachability, delay, offset, jitter = ntp_info.split()

      # 'remote' contains '*' if the machine synchronized with NTP server
      synchronized = "*" in remote

      match = re.search("(\d+\.\d+\.\d+\.\d+)", remote)
      ip = match.group(1)

      ntp_stats.append({
        "remote"      : unicode(ip),
        "referenceid" : unicode(refid),
        "synchronized": synchronized,
        "stratum"     : int(st),
        "type"        : unicode(t),
        "when"        : int(when),
        "hostpoll"    : int(hostpoll),
        "reachability": int(reachability),
        "delay"       : float(delay),
        "offset"      : float(offset),
        "jitter"      : float(jitter)
      })

    return ntp_stats

  
  def get_ntp_peers(self):
    output = self._send_command("ntpq -np", is_vyatta_op=False).split("\n")[2:-1]

    ntp_peers = dict()
  
    for line in output:
      match = re.search("(\d+\.\d+\.\d+\.\d+)\s+", line)
      ntp_peers.update({
        unicode(match.group(1)): {} 
      })

    return ntp_peers

  def get_bgp_neighbors(self):
    # 'description', 'sent_prefixes' and 'received_prefixes' are not implemented yet

    """
    'show ip bgp summary' output example:
    BGP router identifier 192.168.1.2, local AS number 64520
    IPv4 Unicast - max multipaths: ebgp 1 ibgp 1
    RIB entries 3, using 288 bytes of memory
    Peers 3, using 13 KiB of memory

    Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
    192.168.1.1     4 64519    7226    7189        0    0    0 4d23h40m        1
    192.168.1.3     4 64521    7132    7103        0    0    0 4d21h05m        0
    192.168.1.4     4 64522       0       0        0    0    0 never    Active
    """

    output = self._send_command("show ip bgp summary").split("\n")

    match = re.search(".* router identifier (\d+\.\d+\.\d+\.\d+), local AS number (\d+)", output[0])
    router_id = unicode(match.group(1))
    local_as = int(match.group(2)) 

    bgp_neighbor_data = dict()
    bgp_neighbor_data["global"] = dict()
    bgp_neighbor_data["global"]["router_id"] = router_id
    bgp_neighbor_data["global"]["peers"] = {}

    # delete the header and empty element
    bgp_info = [i.strip() for i in output[6:-3] if i is not ""]

    for i in bgp_info:
      peer_id , bgp_version, remote_as, msg_rcvd, msg_sent, table_version, \
      in_queue, out_queue, up_time, state_prefix = i.split()  

      is_enabled = "(Admin)" not in state_prefix

      try:
        state_prefix = int(state_prefix)
        is_up = True
      except ValueError:
        is_up = False

      if bgp_version == "4":
        address_family = "ipv4"
      elif bgp_version == "6":
        address_family = "ipv6"
      else:
        raise ValueError("BGP neighbor parsing failed")
  
      """
      'show ip bgp neighbors 192.168.1.1' output example:
      BGP neighbor is 192.168.1.1, remote AS 64519, local AS 64520, external link
      BGP version 4, remote router ID 192.168.1.1
      For address family: IPv4 Unicast
      ~~~
      Community attribute sent to this neighbor(both)
      1 accepted prefixes
      ~~~
      """
      bgp_detail = self._send_command("show ip bgp neighbors %s" % peer_id)

      match_rid = re.search("remote router ID (\d+\.\d+\.\d+\.\d+).*", bgp_detail)
      remote_rid = match_rid.group(1)

      match_prefix_accepted = re.search("(\d+) accepted prefixes", bgp_detail)
      accepted_prefixes = match_prefix_accepted.group(1)

      bgp_neighbor_data["global"]["peers"].setdefault(peer_id, {})
      peer_dict = {
        "description": "",
        "is_enabled" : is_enabled,
        "local_as"   : local_as,
        "is_up"      : is_up,
        "remote_id"  : unicode(remote_rid),
        "uptime"     : self._bgp_time_conversion(up_time),
        "remote_as"  : int(remote_as)
      }

      af_dict = dict()
      af_dict[address_family] = {
        "sent_prefixes"    : None,
        "accepted_prefixes": int(accepted_prefixes),
        "received_prefixes": None
      }

      peer_dict["address_family"] = af_dict
      bgp_neighbor_data["global"]["peers"][peer_id] = peer_dict

    return bgp_neighbor_data


  def _bgp_time_conversion(self, bgp_uptime):
    uptime_letters = set(["y", "w", "h", "d"])

    if "never" in bgp_uptime:
      return -1
    elif ":" in bgp_uptime:
      hours, minutes, seconds = map(int, bgp_uptime.split(":"))
      return (hours * self._HOUR_SECONDS) + (minutes * self._MINUTE_SECONDS) + seconds
    elif uptime_letters & set(bgp_uptime):
      times = { 
        "y": self._YEAR_SECONDS, 
        "w": self._WEEK_SECONDS, 
        "d": self._DAY_SECONDS,
        "h": self._HOUR_SECONDS 
      }
      match = re.search("(\d+)(\w)(\d+)(\w)(\d+)(\w)", bgp_uptime)
      uptime = int(match.group(1)) * times[match.group(2)] \
             + int(match.group(3)) * times[match.group(4)] \
             + int(match.group(5)) * times[match.group(6)]
    
    return uptime


  def get_interfaces_counters(self):
    # 'rx_unicast_packet', 'rx_broadcast_packets', 'tx_unicast_packets', 
    # 'tx_multicast_packets' and 'tx_broadcast_packets' are not implemented yet

    """
    'show interfaces detail' output example:
    eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether 00:50:56:86:8c:26 brd ff:ff:ff:ff:ff:ff
    ~~~
    RX:  bytes    packets     errors    dropped    overrun      mcast
      35960043     464584          0        221          0        407
    TX:  bytes    packets     errors    dropped    carrier collisions
      32776498     279273          0          0          0          0
    """
    output = self._send_command("show interfaces detail")

    interfaces =  re.findall("(\S+): <.*", output)
    count = re.findall("(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+", output)

    counters = dict()
    
    j = 0 

    for i in count:
      if j % 2 == 0:
        rx_errors = i[2]
        rx_discards = i[3]
        rx_octets = i[0]
        rx_unicast_packets = None
        rx_multicast_packets = i[5]
        rx_broadcast_packets = None
      else:
        counters.update({
          interfaces[j / 2]: {
            "tx_errors"           : i[2],
            "tx_discards"         : i[3],
            "tx_octets"           : i[0],
            "tx_unicast_packets"  : None,
            "tx_multicast_packets": None,
            "tx_broadcast_packets": None,
            "rx_errors"           : rx_errors,
            "rx_discards"         : rx_discards,
            "rx_octets"           : rx_octets,
            "rx_unicast_packets"  : rx_unicast_packets,
            "rx_multicast_packets": rx_multicast_packets,
            "rx_broadcast_packets": rx_broadcast_packets
          }
        })
      j += 1

    return counters


  def get_snmp_information(self):
    # 'acl' is not implemented yet

    output = self._send_command("show configuration")
    # convert the configuration to dictionary 
    config = vyattaconfparser.parse_conf(output)
    
    snmp = dict()
    snmp["community"] = dict()

    for i in config["service"]["snmp"]["community"]:
      snmp["community"].update({
        i: {
          "acl": None,
          "mode": config["service"]["snmp"]["community"][i]["authorization"]
        }
      })

    snmp.update({
      "contact": config["service"]["snmp"]["contact"],
      "location": config["service"]["snmp"]["location"]
    })

    return snmp


  def get_facts(self):
    output = self._send_command("show version").split("\n")
  
    uptime_str = [line for line in output if "Uptime" in line][0]
    uptime = self.parse_uptime(uptime_str)

    ver_str = [line for line in output if "Version" in line][0]
    version = self.parse_version(ver_str)

    sn_str = [line for line in output if "S/N" in line][0]
    snumber = self.parse_snumber(sn_str)

    output = self._send_command("show configuration")
    config = vyattaconfparser.parse_conf(output)

    hostname = config["system"]["host-name"]

    fqdn = config["system"]["domain-name"]    

    iface_list = list()
    for iface_type in config["interfaces"]:
      for iface_name in config["interfaces"][iface_type]:
        iface_list.append(iface_name) 

    facts = {
      "uptime"        : int(uptime),
      "vendor"        : "VyOS",
      "os_version"    : unicode(version),
      "serial_number" : unicode(snumber),
      "model"         : "VyOS",
      "hostname"      : unicode(hostname),
      "fqdn"          : unicode(fqdn),
      "interface_list": iface_list
    }

    return facts


  def _send_command(self, command, is_vyatta_op=True, with_err=False):
    if is_vyatta_op is True:
      op = self._CMD_PREFIX + command
    else:
      op = command

    if with_err is True:
      output = self._device.exec_command(op)[1:3]
      output = [output[0].read(), output[1].read()]
    else:
      output = self._device.exec_command(op)[1].read()

    return output 


  def parse_uptime(self, uptime_str):
    
    uptime_str = uptime_str.split()
    up_days = int(uptime_str[3])
    up_hours, up_minutes, up_seconds = map(int, uptime_str[1].split(":"))
    
    uptime = up_days    * self._DAY_SECONDS    \
           + up_hours   * self._HOUR_SECONDS   \
           + up_minutes * self._MINUTE_SECONDS \
           + up_seconds

    return uptime


  @staticmethod
  def parse_version(ver_str):
    version = ver_str.split()[-1]
    return version


  @staticmethod
  def parse_snumber(sn_str):
    sn = sn_str.split(":")
    return sn[1].strip() 


  def get_interfaces_ip(self):
    output = self._send_command("show interfaces")
    output = output.split("\n")

    # delete the header line and the interfaces which has no ip address
    ifaces = [x for x in output[3:-1] if "-" not in x]

    ifaces_ip = dict()

    for iface in ifaces:
      iface = iface.split()
    
      if len(iface) != 1:

        iface_name = iface[0]

        # Delete the "Interface" column
        iface = iface[1:-1]
        # Key initialization
        ifaces_ip[iface_name] = dict()

      ip_addr, mask = iface[0].split("/")
      ip_ver = self._get_ip_version(ip_addr) 

      # Key initialization
      if ip_ver not in ifaces_ip[iface_name]:
        ifaces_ip[iface_name][ip_ver] = dict()

      ifaces_ip[iface_name][ip_ver][ip_addr] = { "prefix_length": mask }

    return ifaces_ip


  @staticmethod
  def _get_ip_version(ip_address):
    if ":" in ip_address:
      return "ipv6"
    elif "." in ip_address:
      return "ipv4"
  
  
  def get_users(self):
    output = self._send_command("show configuration commands").split("\n")

    user_conf = [x.split() for x in output if "login" in x]

    # Collect all users' name 
    user_name = list(set([x[4] for x in user_conf]))

    user_auth = dict()

    for user in user_name:

      sshkeys = list()

      # extract the configuration which relates to 'user'
      for line in [x for x in user_conf if user in x]:

        # "set system login user alice authentication encrypted-password 'abc'"
        if line[6] == "encrypted-password":
          password = line[7].strip("'")

        # set system login user alice level 'admin'
        elif line[5] == "level":
          if line[6].strip("'") == "admin":
            level = 15
          else:
            level = 0

        # "set system login user alice authentication public-keys alice@example.com key 'ABC'"
        elif len(line) == 10 and line[8] == "key":
          sshkeys.append(line[9].strip("'"))

      user_auth.update({
        user: {
          "level": level,
          "password": password,
          "sshkeys": sshkeys
        }
      })

    return user_auth


  def ping(self, destination, source="", ttl=255, timeout=2, size=100, count=5):
    # does not support multiple destination yet

    command  = "ping %s " % destination
    command += "ttl %d " % ttl
    command += "deadline %d " % timeout
    command += "size %d " % size
    command += "count %d " % count
    if source != "":
      command += "interface %s " % source

    ping_result = dict()

    output, err = self._send_command(command, with_err=True)

    if err is not "":
      ping_result["error"] = err
    else:
      # 'packet_info' example: 
      # ['5', 'packets', 'transmitted,' '5', 'received,' '0%', 'packet', 'loss,', 'time', '3997ms']
      packet_info = output.split("\n")[-3]
      packet_info = [x.strip() for x in packet_info.split()]

      sent = int(packet_info[0])
      received = int(packet_info[3])
      lost = sent - received  

      # 'rtt_info' example:
      # ["0.307/0.396/0.480/0.061"]
      rtt_info = output.split("\n")[-2]
      match = re.search("([\d\.]+)/([\d\.]+)/([\d\.]+)/[\d\.]+", rtt_info)
      
      if match is not None:
        rtt_min = float(match.group(1))
        rtt_avg = float(match.group(2))
      else:
        rtt_min = None
        rtt_avg = None

      ping_result["success"] = dict() 
      ping_result["success"] = {
        "probes_sent": sent,
        "packet_loss": lost,
        "rtt_min"    : rtt_min,
        "rtt_avg"    : rtt_avg,
        "rtt_stdev"  : None,
        "results"    : {"ip_address": destination, "rtt": rtt_avg}
      }

    return ping_result
