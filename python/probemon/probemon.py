#!/usr/bin/python

import time
from datetime import datetime
from datetime import timedelta
import argparse
import netaddr
import sys
import logging
from scapy.all import *
from pprint import pprint
from logging.handlers import RotatingFileHandler
from fuzzywuzzy import process


NAME = 'probemon'
DESCRIPTION = "a command line tool for logging 802.11 probe request frames"
CONFIG_TIME = timedelta(minutes=10)
DEVICE_TTL = timedelta(minutes=6)
RSSI_LIMIT = -60
UNKNOWN_ORG = "UNKNOWN"

DEBUG = True

devices = {}
ignored = {}
start_time = datetime.now()
in_config = True
segment_start = None

def is_randomized(mac, org):
	mask_bit = ['2', '6', 'a', 'e']
	
	return org == UNKNOWN_ORG and mac[1] in mask_bit
	

def is_mobile(mac, org):
	if is_randomized(mac, org):
		return True
	else:
		manufacturers = [
				'Motorola Mobility LLC', 
				'GUANGDONG OPPO MOBILE',
				'Huawei Symantec Technologies',
				'Microsoft',
				'HTC Corporation',
				'Samsung Electronics',
				'SAMSUNG ELECTRO-MECHANICS',
				'Blackberry RTS',
				'LG ELECTRONICS INC',
				'Apple, Inc',
				'OnePlus Tech',
				'Xiaomi Communications'
				]
		name_match = process.extractOne(org, manufacturers)
		# print(str(org) + " => " + str(name_match))
		return name_match[1] > 90
	

def found_device(logger, mac, org, ssid, rssi):
	global segment_start
	if datetime.now() - segment_start > DEVICE_TTL:
		logger.info("**************************************************")
		logger.info("LAST SEGMENT HAD " + str(len(devices)) + " DEVICES")
		logger.info("DETAILS:")
		for details in devices.values():
			logger.info(details)
		devices.clear()
		segment_start = datetime.now()
	if rssi > RSSI_LIMIT and is_mobile(mac, org):
		devices[mac] = device_string(mac, org, ssid, rssi)
	
def device_string(mac, org, ssid, rssi):
	return mac + ", " + org + "[" + ssid + "] dB: " + str(rssi)

def build_packet_callback(logger):
	
	def packet_callback(packet):
		global in_config
		global segment_start
		
		try:
			if not packet.haslayer(Dot11):
				return
			# we are looking for management frames with a probe subtype
			# if neither match we are done here
			if packet.type != 0 or packet.subtype != 0x04:
				return
			
			mac = packet.addr2
			ssid = packet.info
			rssi = packet.dBm_AntSignal
			org = None
			
			# parse mac address and look up the organization from the vendor octets
			try:
				parsed_mac = netaddr.EUI(mac)
				org = parsed_mac.oui.registration().org
			except netaddr.core.NotRegisteredError, e:
				org = UNKNOWN_ORG
				
			if in_config and (datetime.now() - start_time) > CONFIG_TIME:
				in_config = False
				segment_start = datetime.now()
				logger.info("*******ENDING CONFIGURATION MODE***********")
			
			if in_config:
				if not(mac in ignored) and not(is_randomized(mac, org)):
					ignored[mac] = True
					logger.info("IGNORING: " + device_string(mac, org, ssid, rssi))
			elif not(mac in ignored):
				found_device(logger, mac, org, ssid, rssi)
		except Exception as e:
			print(repr(e))
			
			

	return packet_callback

def main():
	parser = argparse.ArgumentParser(description=DESCRIPTION)
	parser.add_argument('-i', '--interface', help="capture interface")
	parser.add_argument('-o', '--output', default='probemon.log', help="logging output location")
	parser.add_argument('-b', '--max-bytes', default=5000000, help="maximum log size in bytes before rotating")
	parser.add_argument('-c', '--max-backups', default=99999, help="maximum number of log files to keep")
	args = parser.parse_args()

	if not args.interface:
		print "error: capture interface not given, try --help"
		sys.exit(-1)
	
	# setup our rotating logger
	logger = logging.getLogger(NAME)
	logger.setLevel(logging.INFO)
	handler = RotatingFileHandler(args.output, maxBytes=args.max_bytes, backupCount=args.max_backups)
	logger.addHandler(handler)
	logger.addHandler(logging.StreamHandler(sys.stdout))
	
	logger.info("*******STARTING CONFIGURATION MODE***********")

	built_packet_cb = build_packet_callback(logger)

	sniff(iface=args.interface, prn=built_packet_cb, store=0)
	

if __name__ == '__main__':
	main()
