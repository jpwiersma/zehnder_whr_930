#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Interface with a StorkAir WHR930

Publish every 10 seconds the status on a MQTT topic
Listen on MQTT topic for commands to set the ventilation level
"""
MQTTBaseTopic='whr960/'

import paho.mqtt.client as mqtt
import time
import sys
import serial
import yaml
from pathlib import Path

def debug_msg(message):
    if debug is True:
        print(
            "{0} DEBUG: {1}".format(
                time.strftime("%d-%m-%Y %H:%M:%S", time.gmtime()), message
            )
        )


def warning_msg(message):
    if warning is True:
        print(
            "{0} WARNING: {1}".format(
                time.strftime("%d-%m-%Y %H:%M:%S", time.gmtime()), message
            )
        )


def info_msg(message):
    print(
        "{0} INFO: {1}".format(
            time.strftime("%d-%m-%Y %H:%M:%S", time.gmtime()), message
        )
    )


def debug_data(serial_data):
    if not debug is True:
        return

    if not serial_data is None:

        if debug_level > 0:
            data_len = len(serial_data)
            if data_len == 2 and serial_data[0] == "07" and serial_data[1] == "f3":
                debug_msg(
                    "Recieved an ack packet: {0} {1}".format(serial_data[0], serial_data[1])
                )
            else:
                debug_msg("Data length   : {0}".format(len(serial_data)))
                debug_msg("Ack           : {0} {1}".format(serial_data[0], serial_data[1]))
                debug_msg("Start         : {0} {1}".format(serial_data[2], serial_data[3]))
                debug_msg("Command       : {0} {1}".format(serial_data[4], serial_data[5]))
                debug_msg(
                    "Nr data bytes : {0} (integer {1})".format(
                        serial_data[6], int(serial_data[6], 16)
                    )
                )

                n = 1
                while n <= int(serial_data[6], 16):
                    debug_msg(
                        "Data byte {0}   : Hex: {1}, Int: {2}, Array #: {3}".format(
                            n, serial_data[n + 6], int(serial_data[n + 6], 16), n + 6
                        )
                    )
                    n += 1

                debug_msg("Checksum      : {0}".format(serial_data[-2]))
                debug_msg("End           : {0} {1}".format(serial_data[-2], serial_data[-1]))

        if debug_level > 1:
            n = 0
            while n < data_len:
                debug_msg("serial_data {0}   : {1}".format(n, serial_data[n]))
                n += 1

    else:
        debug_msg("serial_data is empty")

def publish_message(msg, mqtt_path):
    mqttc.publish(mqtt_path, payload=msg, qos=0, retain=True)
    time.sleep(0.1)
    debug_msg(
        "published message {0} on topic {1} at {2}".format(
            msg, mqtt_path, time.asctime(time.localtime(time.time()))
        )
    )


def create_packet(command, data=[]):
    """
    Create a packet.
    Data length and checksum are automatically calculated and added to the packet.
    Start and end bits are added as well.

    A packet is build up as follow:

        Start                : 2 bytes (0x07 0xF0)
        Command              : 2 bytes
        Number of data bytes : 1 byte
        Data bytes           : 0-n bytes
        Checksum             : 1 byte
        End                  : 2 bytes (0x07 0x0F)
    """
    packet = []
    packet.append(0x07)  # default start bit
    packet.append(0xF0)  # default start bit

    for b in command:
        packet.append(b)

    packet.append(len(data))
    for b in data:
        packet.append(b)

    packet.append(calculate_checksum(packet[2:]))
    packet.append(0x07)  # default end bit
    packet.append(0x0F)  # default end bit

    return bytes(packet)


def calculate_checksum(data):
    """
    The checksum is obtained by adding all bytes (excluding start and end) plus 173.
    If the value 0x07 appears twice in the data area, only one 0x07 is used for the checksum calculation.
    If the checksum is larger than one byte, the least significant byte is used.
    """
    checksum = 173
    found_07 = False

    for b in data:
        if (b == 0x07 and found_07 == False) or b != 0x07:
            checksum += b

        if b == 0x07:
            found_07 = True

        if checksum > 0xFF:
            checksum -= 0xFF + 1

    return checksum


def calculate_incoming_checksum(data_raw):
    """The checksum over incoming data is calculated over the bytes starting from the default start bytes to the checksum value"""
    int_data = []
    for b in data_raw[4:-3]:
        int_data.append(int.from_bytes(b, "big"))
    return calculate_checksum(int_data)


def validate_data(data_raw):
    """Incoming data is in raw bytes. Convert to hex values for easier processing"""
    data = []
    for raw in data_raw:
        data.append(raw.hex())

    if len(data) <= 1:
        """always expect a valid ACK at least"""
        return None

    if len(data) == 2 and data[0] == "07" and data[1] == "f3":
        """
        This is a regular ACK which is received on all "setting" commands,
        such as setting ventilation level, command 0x99)
        """
        return data
    else:
        if len(data) >= 10:
            """If the data is more than a regular ACK, validate the checksum"""
            checksum = calculate_incoming_checksum(data_raw)
            if checksum != int.from_bytes(data_raw[-3], "big"):
                warning_msg(
                    "Checksum doesn't match ({} vs {}). Message ignored".format(
                        checksum, int.from_bytes(data_raw[-3], "big")
                    )
                )
                return None
            """
            A valid response should be at least 10 bytes (ACK + response with data length = 0)

            Byte 6 in the array contains the length of the dataset. This length + 10 is the total
            size of the message
            """
            dataset_len = int(data[6], 16)
            message_len = dataset_len + 10
            debug_msg("Message length is {}".format(message_len))

            """ 
            Sometimes more data is captured on the serial port then we expect. We drop those extra
            bytes to get a clean data to work on
            """
            stripped_data = data[0:message_len]
            debug_msg("Stripped message length is {}".format(len(stripped_data)))

            if (
                stripped_data[0] != "07"
                or stripped_data[1] != "f3"
                or stripped_data[2] != "07"
                or stripped_data[3] != "f0"
                or stripped_data[-2] != "07"
                or stripped_data[-1] != "0f"
            ):
                warning_msg("Received garbage data, ignored ...")
                debug_data(stripped_data)
                return None
            else:
                debug_msg("Serial data validation passed")
                """
                Since we are here, we have a clean data set. Now we need to remove
                a double 0x07 in the dataset if present. This must be done because
                according the protocol specification, when a 0x07 value appears in
                the dataset, another 0x07 is inserted, but not added to the length
                or the checksum
                """
                try:
                    for i in range(7, 6 + dataset_len):
                        if stripped_data[i] == "07" and stripped_data[i + 1] == "07":
                            del stripped_data[i + 1]

                    return stripped_data
                except IndexError as _err:
                    """
                    The previous operation has thrown an IndexError which probably is
                    the result of a missing second '07'. We just issue a warning message
                    and return the stripped_data set
                    """
                    warning_msg(
                        "validate_data function got an IndexError, but we continued processing the data: {}".format(
                            _err
                        )
                    )

        else:
            warning_msg(
                "The length of the data we received from the serial port is {}, it should be minimal 10 bytes".format(
                    len(data)
                )
            )
            return None


def serial_command(cmd):
    data = []
    ser.write(cmd)
    time.sleep(2)

    while ser.inWaiting() > 0:
        data.append(ser.read(1))

    return validate_data(data)


def status_8bit(inp):
    """
    Return the status of each bit in a 8 byte status
    """
    idx = 7
    matches = {}

    for num in (2 ** p for p in range(idx, -1, -1)):
        if ((inp - num) > 0) or ((inp - num) == 0):
            inp = inp - num
            matches[idx] = True
        else:
            matches[idx] = False

        idx -= 1

    return matches


def set_ventilation_level(fan_level):
    """
    Command: 0x00 0x99
    """
    if fan_level < 0 or fan_level > 3:
        info_msg(
            "Ventilation level can be set to 0, 1, 2 and 4, but not {0}".format(
                fan_level
            )
        )
        return None

    packet = create_packet([0x00, 0x99], [fan_level + 1])
    data = serial_command(packet)
    debug_data(data)

    if data:
        if data[0] == "07" and data[1] == "f3":
            info_msg("Changed the ventilation to {0}".format(fan_level))
        else:
            warning_msg(
                "Changing the ventilation to {0} went wrong, did not receive an ACK after the set command".format(
                    fan_level
                )
            )
    else:
        warning_msg(
            "Changing the ventilation to {0} went wrong, did not receive an ACK after the set command".format(
                fan_level
            )
        )


def set_comfort_temperature(temperature):
    """
    Command: 0x00 0xD3
    """
    calculated_temp = int(temperature + 20) * 2

    if temperature < 12 or temperature > 28:
        warning_msg(
            "Changing the comfort temperature to {} is outside the specification of the range min 12 and max 28".format(
                temperature
            )
        )
        return None

    packet = create_packet([0x00, 0xD3], [calculated_temp])
    data = serial_command(packet)
    debug_data(data)

    if data:
        if data[0] == "07" and data[1] == "f3":
            info_msg("Changed comfort temperature to {0}".format(temperature))
        else:
            warning_msg(
                "Changing the comfort temperature to {0} went wrong, did not receive an ACK after the set command".format(
                    temperature
                )
            )
    else:
        warning_msg(
            "Changing the comfort temperature to {0} went wrong, did not receive an ACK after the set command".format(
                temperature
            )
        )


def get_temp():
    """
    Command: 0x00 0xD1
    """
    packet = create_packet([0x00, 0xD1])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_temp function could not get serial data")
        else:
            """
            The default comfort temperature of the WHR930 is 20c

            Zehnder advises to let it on 20c, but if you want you change it, to
            set it to 21c in the winter and 15c in the summer.
            """
            ComfortTemp = int(data[7], 16) / 2.0 - 20
            OutsideAirTemp = int(data[8], 16) / 2.0 - 20
            SupplyAirTemp = int(data[9], 16) / 2.0 - 20
            ReturnAirTemp = int(data[10], 16) / 2.0 - 20
            ExhaustAirTemp = int(data[11], 16) / 2.0 - 20

            publish_message(msg=ComfortTemp, mqtt_path=MQTTBaseTopic + 'tele/temperature/comfort')
            publish_message(
                msg=OutsideAirTemp, mqtt_path=MQTTBaseTopic + 'tele/temperature/outside_air'
            )
            publish_message(
                msg=SupplyAirTemp, mqtt_path=MQTTBaseTopic + 'tele/temperature/supply_air'
            )
            publish_message(
                msg=ReturnAirTemp, mqtt_path=MQTTBaseTopic + 'tele/temperature/return_air'
            )
            publish_message(
                msg=ExhaustAirTemp, mqtt_path=MQTTBaseTopic + 'tele/temperature/exhaust_air'
            )

            debug_msg(
                "ComfortTemp: {0}, OutsideAirTemp: {1}, SupplyAirTemp: {2}, ReturnAirTemp: {3}, ExhaustAirTemp: {4}".format(
                    ComfortTemp,
                    OutsideAirTemp,
                    SupplyAirTemp,
                    ReturnAirTemp,
                    ExhaustAirTemp,
                )
            )
    except IndexError:
        warning_msg("get_temp ignoring incomplete message")


def get_ventilation_status():
    """
    Command: 0x00 0xCD
    """
    status_data = {"IntakeFanActive": {0: False, 1: True}}

    packet = create_packet([0x00, 0xCD])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_ventilation_status function could not get serial data")
        else:
            ReturnAirLevel = int(data[13], 16)
            SupplyAirLevel = int(data[14], 16)
            FanLevel = int(data[15], 16) - 1
            IntakeFanActive = status_data["IntakeFanActive"][int(data[16], 16)]

            publish_message(
                msg=ReturnAirLevel, mqtt_path=MQTTBaseTopic + 'tele/level/return_air'
            )
            publish_message(
                msg=SupplyAirLevel, mqtt_path=MQTTBaseTopic + 'tele/level/supply_air'
            )
            publish_message(
                msg=FanLevel, mqtt_path=MQTTBaseTopic + 'tele/level/fan'
            )
            publish_message(
                msg=IntakeFanActive, mqtt_path=MQTTBaseTopic + 'tele/fan/intake_active'
            )
            debug_msg(
                "ReturnAirLevel: {}, SupplyAirLevel: {}, FanLevel: {}, IntakeFanActive: {}".format(
                    ReturnAirLevel, SupplyAirLevel, FanLevel, IntakeFanActive
                )
            )
    except IndexError:
        warning_msg("get_ventilation_status ignoring incomplete message")


def get_fan_status():
    """
    Command: 0x00 0x99
    """
    packet = create_packet([0x00, 0x0B])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_fan_status function could not get serial data")
        else:
            IntakeFanSpeed = int(data[7], 16)
            ExhaustFanSpeed = int(data[8], 16)
            IntakeFanRPM = int(1875000 / (int(data[9], 16) * 256 + int(data[10], 16)))
            ExhaustFanRPM = int(1875000 / (int(data[11], 16) * 256 + int(data[12], 16)))

            publish_message(
                msg=IntakeFanSpeed, mqtt_path=MQTTBaseTopic + 'tele/fan/intake_speed'
            )
            publish_message(
                msg=ExhaustFanSpeed, mqtt_path=MQTTBaseTopic + 'tele/fan/exhaust_speed'
            )
            publish_message(
                msg=IntakeFanRPM, mqtt_path=MQTTBaseTopic + 'tele/fan/intake_speed_rpm'
            )
            publish_message(
                msg=ExhaustFanRPM, mqtt_path=MQTTBaseTopic + 'tele/fan/exhaust_speed_rpm'
            )

            debug_msg(
                "IntakeFanSpeed {0}%, ExhaustFanSpeed {1}%, IntakeAirRPM {2}, ExhaustAirRPM {3}".format(
                    IntakeFanSpeed, ExhaustFanSpeed, IntakeFanRPM, ExhaustFanRPM
                )
            )
    except IndexError:
        warning_msg("get_fan_status ignoring incomplete message")


def get_filter_status():
    """
    Command: 0x00 0xD9
    """
    packet = create_packet([0x00, 0xD9])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_filter_status function could not get serial data")
        else:
            if int(data[15], 16) == 0:
                FilterStatus = "Ok"
            elif int(data[15], 16) == 1:
                FilterStatus = "Full"
            else:
                FilterStatus = "Unknown"

            publish_message(
                msg=FilterStatus, mqtt_path=MQTTBaseTopic + 'tele/filter_status'
            )
            debug_msg("FilterStatus: {0}".format(FilterStatus))
    except IndexError:
        warning_msg("get_filter_status ignoring incomplete message")


def get_valve_status():
    """
    Command: 0x00 0x0D
    """
    packet = create_packet([0x00, 0x0D])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_valve_status function could not get serial data")
        else:
            ByPass = int(data[7], 16)
            """
            Status of the pre heating valve is exposed in get_preheating_status by the variable PreHeatingValveStatus
            PreHeating = int(data[8], 16)
            """
            ByPassMotorCurrent = int(data[9], 16)
            PreHeatingMotorCurrent = int(data[10], 16)

            publish_message(
                msg=ByPass, mqtt_path=MQTTBaseTopic + 'tele/valve/bypass_percentage'
            )
            publish_message(
                msg=ByPassMotorCurrent,
                mqtt_path=MQTTBaseTopic + 'tele/bypass/motor_current'
            )
            publish_message(
                msg=PreHeatingMotorCurrent,
                mqtt_path=MQTTBaseTopic + 'tele/preheating/motor_current',
            )

            debug_msg(
                "ByPass: {}, ByPassMotorCurrent: {}, PreHeatingMotorCurrent: {}".format(
                    ByPass, ByPassMotorCurrent, PreHeatingMotorCurrent
                )
            )
    except IndexError:
        warning_msg("get_valve_status ignoring incomplete message")


def get_bypass_control():
    """
    Command: 0x00 0xDF
    """
    packet = create_packet([0x00, 0xDF])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_bypass_control function could not get serial data")
        else:
            ByPassFactor = int(data[9], 16)
            ByPassStep = int(data[10], 16)
            ByPassCorrection = int(data[11], 16)

            if int(data[13], 16) == 1:
                SummerMode = True
            else:
                SummerMode = False

            publish_message(
                msg=ByPassFactor, mqtt_path=MQTTBaseTopic + 'tele/bypass/factor'
            )
            publish_message(msg=ByPassStep, mqtt_path=MQTTBaseTopic + 'tele/bypass/step')
            publish_message(
                msg=ByPassCorrection, mqtt_path=MQTTBaseTopic + 'tele/bypass/correction'
            )
            publish_message(msg=SummerMode, mqtt_path=MQTTBaseTopic + 'tele/bypass/summermode')

            debug_msg(
                "ByPassFactor: {}, ByPassStep: {}, ByPassCorrection: {}, SummerMode: {}".format(
                    ByPassFactor, ByPassStep, ByPassCorrection, SummerMode
                )
            )
    except IndexError:
        warning_msg("get_bypass_control ignoring incomplete message")


def get_preheating_status():
    """
    Command: 0x00 0xE1
    """
    status_data = {
        "PreHeatingValveStatus": {0: "Closed", 1: "Open", "2": "Unknown"},
        "FrostProtectionActive": {0: False, 1: True},
        "PreHeatingActive": {0: False, 1: True},
        "FrostProtectionLevel": {
            0: "GuaranteedProtection",
            1: "HighProtection",
            2: "NominalProtection",
            3: "Economy",
        },
    }

    packet = create_packet([0x00, 0xE1])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_preheating_status function could not get serial data")
        else:
            PreHeatingValveStatus = status_data["PreHeatingValveStatus"][
                int(data[7], 16)
            ]
            FrostProtectionActive = status_data["FrostProtectionActive"][
                int(data[8], 16)
            ]
            PreHeatingActive = status_data["PreHeatingActive"][int(data[9], 16)]
            FrostProtectionMinutes = int(data[10], 16) + int(data[11], 16)
            FrostProtectionLevel = status_data["FrostProtectionLevel"][
                int(data[12], 16)
            ]

            publish_message(
                msg=PreHeatingValveStatus,
                mqtt_path=MQTTBaseTopic + 'tele/preheating/valve'
            )
            publish_message(
                msg=FrostProtectionActive,
                mqtt_path=MQTTBaseTopic + 'tele/preheating/frost_protection'
            )
            publish_message(
                msg=PreHeatingActive, mqtt_path=MQTTBaseTopic + 'tele/preheating/state'
            )
            publish_message(
                msg=FrostProtectionMinutes,
                mqtt_path=MQTTBaseTopic + 'tele/preheating/frost_protection_minutes'
            )
            publish_message(
                msg=FrostProtectionLevel,
                mqtt_path=MQTTBaseTopic + 'tele/preheating/frost_protection_level'
            )

            debug_msg(
                "PreHeatingValveStatus: {}, FrostProtectionActive: {}, PreHeatingActive: {}, FrostProtectionMinutes: {}, FrostProtectionLevel: {}".format(
                    PreHeatingValveStatus,
                    FrostProtectionActive,
                    PreHeatingActive,
                    FrostProtectionMinutes,
                    FrostProtectionLevel,
                )
            )
    except IndexError:
        warning_msg("get_preheating_status ignoring incomplete message")
    except KeyError as _err:
        warning_msg(
            "get_preheating_status incomplete message, missing a key: {}".format(_err)
        )


def get_operating_hours():
    """
    Command: 0x00 0xDD
    """
    packet = create_packet([0x00, 0xDD])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_operating_hours function could not get serial data")
        else:
            Level0Hours = int(data[7], 16) + int(data[8], 16) + int(data[9], 16)
            Level1Hours = int(data[10], 16) + int(data[11], 16) + int(data[12], 16)
            Level2Hours = int(data[13], 16) + int(data[14], 16) + int(data[15], 16)
            Level3Hours = int(data[24], 16) + int(data[25], 16) + int(data[26], 16)
            FrostProtectionHours = int(data[16], 16) + int(data[17], 16)
            PreHeatingHours = int(data[18], 16) + int(data[19], 16)
            BypassOpenHours = int(data[14], 16) + int(data[15], 16)
            FilterHours = int(data[22], 16) + int(data[23], 16)

            publish_message(msg=Level0Hours, mqtt_path=MQTTBaseTopic + 'tele/hours/level0')
            publish_message(msg=Level1Hours, mqtt_path=MQTTBaseTopic + 'tele/hours/level1')
            publish_message(msg=Level2Hours, mqtt_path=MQTTBaseTopic + 'tele/hours/level2')
            publish_message(msg=Level3Hours, mqtt_path=MQTTBaseTopic + 'tele/hours/level3')
            publish_message(
                msg=FrostProtectionHours,
                mqtt_path="tele/hours/frost_protection",
            )
            publish_message(
                msg=PreHeatingHours, mqtt_path=MQTTBaseTopic + 'tele/hours/preheating'
            )
            publish_message(
                msg=BypassOpenHours, mqtt_path=MQTTBaseTopic + 'tele/hours/bypass'
            )
            publish_message(msg=FilterHours, mqtt_path=MQTTBaseTopic + 'tele/hours/filter')

            debug_msg(
                "Level0Hours: {}, Level1Hours: {}, Level2Hours: {}, Level3Hours: {}, FrostProtectionHours: {}, PreHeatingHours: {}, BypassOpenHours: {}, FilterHours: {}".format(
                    Level0Hours,
                    Level1Hours,
                    Level2Hours,
                    Level3Hours,
                    FrostProtectionHours,
                    PreHeatingHours,
                    BypassOpenHours,
                    FilterHours,
                )
            )
    except IndexError:
        warning_msg("get_operating_hours ignoring incomplete message")


def get_status():
    """
    Command: 0x00 0xD5
    """
    status_data = {
        "PreHeatingPresent": {0: False, 1: True},
        "ByPassPresent": {0: False, 1: True},
        "Type": {2: "Right", 1: "Left"},
        "Size": {1: "Large", 2: "Small"},
        "OptionsPresent": {0: False, 1: True},
        "EWTPresent": {0: False, 1: "Managed", 2: "Unmanaged"},
    }

    active1_status_data = {
        0: "P10",
        1: "P11",
        2: "P12",
        3: "P13",
        4: "P14",
        5: "P15",
        6: "P16",
        7: "P17",
    }

    active2_status_data = {0: "P18", 1: "P19"}

    active3_status_data = {
        0: "P90",
        1: "P91",
        2: "P92",
        3: "P93",
        4: "P94",
        5: "P95",
        6: "P96",
        7: "P97",
    }

    packet = create_packet([0x00, 0xD5])
    data = serial_command(packet)
    debug_data(data)

    try:
        if data is None:
            warning_msg("get_status function could not get serial data")
        else:
            try:
                PreHeatingPresent = status_data["PreHeatingPresent"][int(data[7])]
                ByPassPresent = status_data["ByPassPresent"][int(data[8])]
                Type = status_data["Type"][int(data[9])]
                Size = status_data["Size"][int(data[10])]
                OptionsPresent = status_data["OptionsPresent"][int(data[11])]
                ActiveStatus1 = int(data[13])  # (0x01 = P10 ... 0x80 = P17)
                ActiveStatus2 = int(data[14])  # (0x01 = P18 / 0x02 = P19)
                ActiveStatus3 = int(data[15])  # (0x01 = P90 ... 0x80 = P97)
                EWTPresent = status_data["EWTPresent"][int(data[17])]
            except ValueError as _value_err:
                warning_msg(
                    "get_status function received an inappropriate value: {}".format(
                        _value_err
                    )
                )
            except KeyError as _key_err:
                warning_msg(
                    "get status function missing key in dataset: {}".format(_key_err)
                )

            debug_msg(
                "PreHeatingPresent: {}, ByPassPresent: {}, Type: {}, Size: {}, OptionsPresent: {}, EWTPresent: {}".format(
                    PreHeatingPresent,
                    ByPassPresent,
                    Type,
                    Size,
                    OptionsPresent,
                    EWTPresent,
                )
            )

            for key, value in status_8bit(ActiveStatus1).items():
                topic = MQTTBaseTopic + 'tele/status/{}_active'.format(active1_status_data[key])
                debug_msg("{}: {}".format(topic, value))
                publish_message(msg=value, mqtt_path=topic)

            for key, value in status_8bit(ActiveStatus2).items():
                try:
                    topic = MQTTBaseTopic + 'tele/status/{}_active'.format(
                        active2_status_data[key]
                    )
                    debug_msg("{}: {}".format(topic, value))
                    publish_message(msg=value, mqtt_path=topic)
                except KeyError:
                    pass

            for key, value in status_8bit(ActiveStatus3).items():
                topic = MQTTBaseTopic + 'tele/status/{}_active'.format(active3_status_data[key])
                debug_msg("{}: {}".format(topic, value))
                publish_message(msg=value, mqtt_path=topic)

            publish_message(
                msg=PreHeatingPresent, mqtt_path=MQTTBaseTopic + 'tele/status/preheating_present'
            )
            publish_message(
                msg=ByPassPresent, mqtt_path=MQTTBaseTopic + 'tele/status/bypass_present'
            )
            publish_message(msg=Type, mqtt_path=MQTTBaseTopic + 'tele/status/type')
            publish_message(msg=Size, mqtt_path=MQTTBaseTopic + 'tele/status/size')
            publish_message(
                msg=OptionsPresent, mqtt_path=MQTTBaseTopic + 'tele/status/options_present'
            )
            publish_message(msg=EWTPresent, mqtt_path=MQTTBaseTopic + 'tele/status/ewt_present')
    except IndexError:
        warning_msg("get_status ignoring incomplete message")


def on_message(client, userdata, message):
    debug_msg(
        "message received: topic: {0}, payload: {1}, userdata: {2}".format(
            message.topic, message.payload, userdata
        )
    )

    pending_commands.append(message)


def handle_commands():

    while len(pending_commands) > 0:
        message = pending_commands.pop(0)
        if message.topic == MQTTBaseTopic + "cmd/set_ventilation_level":
            fan_level = int(float(message.payload))
            set_ventilation_level(fan_level)
            get_ventilation_status()
        elif message.topic == MQTTBaseTopic + "cmd/set_comfort_temperature":
            temperature = float(message.payload)
            set_comfort_temperature(temperature)
            get_temp()
        else:
            info_msg(
                "Received a message on topic {} where we do not have a handler for at the moment".format(
                    message.topic
                )
            )


def recon():
    try:
        mqttc.reconnect()
        info_msg("Successfull reconnected to the MQTT server")
        topic_subscribe()
    except:
        warning_msg(
            "Could not reconnect to the MQTT server. Trying again in 10 seconds"
        )
        time.sleep(10)
        recon()


def topic_subscribe():
    try:
        mqttc.subscribe(
            [
                (MQTTBaseTopic + "cmd/set_ventilation_level", 0),
                (MQTTBaseTopic + "cmd/set_comfort_temperature", 0),
            ]
        )
        info_msg("Successfull subscribed to the MQTT topics")
    except:
        warning_msg(
            "There was an error while subscribing to the MQTT topic(s), trying again in 10 seconds"
        )
        time.sleep(10)
        topic_subscribe()


def on_connect(client, userdata, flags, rc):
    topic_subscribe()


def on_disconnect(client, userdata, rc):
    if rc != 0:
        warning_msg("Unexpected disconnection from MQTT, trying to reconnect")
        recon()


def main():
    global debug
    global debug_level
    global warning
    global mqttc
    global ser
    global pending_commands

    with Path(__file__).with_name("config.yaml").open("r") as f:
        config = yaml.safe_load(f.read())

    debug = config["debug"]
    debug_level = 0
    warning = config["warning"]

    pending_commands = []

    """Connect to the MQTT broker"""
    mqttc = mqtt.Client("whr930")
    mqttc.username_pw_set(username=config["mqtt_username"], password=config["mqtt_password"])
    mqttc.tls_set()

    """Define the mqtt callbacks"""
    mqttc.on_connect = on_connect
    mqttc.on_message = on_message
    mqttc.on_disconnect = on_disconnect

    """Connect to the MQTT server"""
    mqttc.connect(config["mqtt_server"], port=config["mqtt_port"], keepalive=45)

    """Open the serial port"""
    ser = serial.Serial(
        port=config["port"],
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
    )

    mqttc.loop_start()

    functions = [
        get_temp,
        get_ventilation_status,
        get_filter_status,
        get_fan_status,
        get_bypass_control,
        get_valve_status,
        get_status,
        get_operating_hours,
        get_preheating_status,
    ]

    while True:
        try:
            for func in functions:
                if len(pending_commands) == 0:
                    debug_msg('Executing function {}'.format(func))
                    func()
                else:
                    handle_commands()

            time.sleep(10)
            pass
        except KeyboardInterrupt:
            mqttc.loop_stop()
            ser.close()
            break


if __name__ == "__main__":
    sys.exit(main())

"""End of program"""
