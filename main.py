import meshtastic.ble_interface
import meshtastic.serial_interface
from pubsub import pub
import time
import math
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Secrets and settings
from env import *

interface = meshtastic.serial_interface.SerialInterface()

# InfluxDB client object
client = InfluxDBClient(url=INFLUXDB_HOST, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG, verify_ssl=INFLUXDB_VERIFYSSL)

nodes = {}
packets_received = 0


def getDewPoint(t, rh):
    a = 17.625 
    b = 243.04 # °C

    alpha = math.log(rh/100) + a * t/(b + t)

    return (b * alpha) / (a - alpha)


def updateNodes():
    global nodes

    if (interface.nodes):
        for node in interface.nodes.values():
            nodes[node['user']['id']] = {
                "short_name": node['user']['shortName'],
                "long_name": node['user']['longName'],
                "hardware": node['user']['hwModel'],
                "mac": None if "macaddr" not in node['user'] else node['user']['macaddr']
            }


def prepareAndSendTransmissionData(packet):
    data = []

    pkt_time = int(time.time()) if 'rxTime' not in list(packet.keys()) else packet['rxTime']

    for key in TRANSMISSION_QUALITY:
        if(key in list(packet.keys())):
            measurement = {
                "measurement":      "telemetry",
                "tags" : {
                    "node_id":      str(packet['fromId']),
                    "type":         str(key),
                    "field":        str("transmissionMetrics"),
                    "node_short":   str(nodes[packet['fromId']]['short_name']),
                    "node_long":    str(nodes[packet['fromId']]['long_name']),
                    "node_hw":      str(nodes[packet['fromId']]['hardware']),
                    "node_mac":     str(nodes[packet['fromId']]['mac']),
                    "node_int":     str(packet['from'])
                },
                "fields" : {
                    "value":        float(packet[key]),
                    "time":         datetime.fromtimestamp(pkt_time, tz=timezone.utc).isoformat(),
                }
            }
            data.append(measurement)

    sendDataToInfluxDB(data)


def prepareAndSendTelemetryData(packet, metricType):
    data = []

    telemetry_keys = list(packet['decoded']['telemetry'].keys())
    metrics = list(packet['decoded']['telemetry'][metricType].keys())

    pkt_time = int(time.time()) if "time" not in telemetry_keys else packet['decoded']['telemetry']['time']

    # Calculating Dew Point if possible
    if(metricType == "environmentMetrics" and all(x in metrics for x in ['temperature', 'relativeHumidity'])):
        temp = packet['decoded']['telemetry'][metricType]['temperature']
        rhum = packet['decoded']['telemetry'][metricType]['relativeHumidity']

        packet['decoded']['telemetry'][metricType]['dewPoint'] = getDewPoint(t=temp, rh=rhum)

    for key in packet['decoded']['telemetry'][metricType]:
        measurement = {
            "measurement":      "telemetry",
            "tags" : {
                "node_id":      str(packet['fromId']),
                "type":         str(key),
                "field":        str(metricType),
                "node_short":   str(nodes[packet['fromId']]['short_name']),
                "node_long":    str(nodes[packet['fromId']]['long_name']),
                "node_hw":      str(nodes[packet['fromId']]['hardware']),
                "node_mac":     str(nodes[packet['fromId']]['mac']),
                "node_int":     str(packet['from'])
            },
            "fields" : {
                "value":        float(packet['decoded']['telemetry'][metricType][key]),
                "time":         datetime.fromtimestamp(pkt_time, tz=timezone.utc).isoformat(),
            }
        }
        data.append(measurement)


    sendDataToInfluxDB(data)


def prepareAndSendPositionData(packet):
    data = []

    pkt_time = int(time.time()) if 'rxTime' not in list(packet.keys()) else packet['rxTime']

    for key in ['latitude', 'longitude']:
        measurement = {
            "measurement":      "telemetry",
            "tags" : {
                "node_id":      str(packet['fromId']),
                "type":         str(key),
                "field":        str("position"),
                "node_short":   str(nodes[packet['fromId']]['short_name']),
                "node_long":    str(nodes[packet['fromId']]['long_name']),
                "node_hw":      str(nodes[packet['fromId']]['hardware']),
                "node_mac":     str(nodes[packet['fromId']]['mac']),
                "node_int":     str(packet['from'])
            },
            "fields" : {
                "value":        float(packet['decoded']['position'][key]),
                "time":         datetime.fromtimestamp(pkt_time, tz=timezone.utc).isoformat(),
            }
        }
        data.append(measurement)

    sendDataToInfluxDB(data)


def prepareAndSendMessageData(packet):
    data = []

    pkt_time = int(time.time()) if 'rxTime' not in list(packet.keys()) else packet['rxTime']

    measurement = {
        "measurement":      "message",
        "tags" : {
            "node_id":      str(packet['fromId']),
            "type":         str("text"),
            "field":        str("message"),
            "node_short":   str(nodes[packet['fromId']]['short_name']),
            "node_long":    str(nodes[packet['fromId']]['long_name']),
            "node_hw":      str(nodes[packet['fromId']]['hardware']),
            "node_mac":     str(nodes[packet['fromId']]['mac']),
            "node_int":     str(packet['from'])
        },
        "fields" : {
            "value":        packet['decoded']['text'],
            "time":         datetime.fromtimestamp(pkt_time, tz=timezone.utc).isoformat(),
        }
    }
    data.append(measurement)

    sendDataToInfluxDB(data)


def sendDataToInfluxDB(prepared_data):

    send_attempts, count_attempts = 3, 0
    while count_attempts < send_attempts:

        try:
            client.write_api(write_options=SYNCHRONOUS).write(bucket=INFLUXDB_DB, org=INFLUXDB_ORG, record=prepared_data, write_precision=WritePrecision.S)
            break
        except Exception as e:
            count_attempts = count_attempts + 1
            time.sleep(10)


def onReceive(packet, interface):

    global packets_received
    packets_received += 1

    if(packets_received % UPDATE_NODES_INTERVAL == 1):
        updateNodes()
        print(f'Packet #{packets_received} / {datetime.now()}')


    prepareAndSendTransmissionData(packet)
    
    if(packet['decoded']['portnum'] == "TELEMETRY_APP"):
        for key in SUPPORTED_METRICS:
            if(key in list(packet['decoded']['telemetry'].keys())):
                prepareAndSendTelemetryData(packet, key)   
                
    elif(packet['decoded']['portnum'] == "POSITION_APP"):
        prepareAndSendPositionData(packet)

    elif(packet['decoded']['portnum'] == "TEXT_MESSAGE_APP"):
        prepareAndSendMessageData(packet)


if __name__ == '__main__':

    pub.subscribe(onReceive, 'meshtastic.receive')

    while True:
        time.sleep(60)
