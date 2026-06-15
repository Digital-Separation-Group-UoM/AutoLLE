import ctypes
import os
import numpy as np
from ctypes import *
import csv

#Load dll library
# Load the DLL and locate the connected spectrometer (finds how many devices and their channel numbers).
print ("Load dll library")
pDll = WinDLL("./FLA5000DLL.dll")

# Key DLL functions used
# JF_USB_FindSpectorMeter — detects connected spectrometers
# JF_USB_SetIntegrationTimeFMux — sets exposure time
# JF_USB_GetWaveDataMux — raw intensity readout (used during optimization)
# JF_USB_GetZerolineMux — dark/zero baseline
# JF_USB_GetBaselineMux — reference baseline
# JF_USB_GetAbsorbanceMux / GetReflectivityMux / GetTransmittanceMux — the three measurement modes

pDll.JF_USB_FindSpectorMeter.restype = None
pDll.JF_USB_FindSpectorMeter.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]

# Get spectrometer channel number
iSpectroCount = ctypes.c_int()
iChannelNumList = (ctypes.c_int * 10)()  
pDll.JF_USB_FindSpectorMeter(ctypes.byref(iSpectroCount), iChannelNumList)

# Handle the result returned by the function
print("Number of spectrometers: " + str(iSpectroCount.value))
print("Spectrometer channel number: " + str(iChannelNumList[0]))

# Set integration time
# Set integration time — how long the sensor collects light per reading (like camera exposure).
fCCDTime = c_float(100)
print("Set integration time:", fCCDTime.value)
res = pDll.JF_USB_SetIntegrationTimeFMux(1, fCCDTime)
print("Returned: " + str(res))

input("Press any key to start determining the optimal integration time\n")

# Find the optimal integration time
# Optimize integration time — loops until the signal peak sits in a healthy range.
# If the peak is above 90% of the max value (65535), exposure is too high (risk of saturation);
# below 10%, too low (weak signal). You adjust until it's in the sweet spot.
while True:
    fStartWave=c_float(350)
    fEndWave=c_float(950)
    fInterval=c_float(1)
    INPUT= c_float*601
    fWaveData = INPUT()
    res=pDll.JF_USB_SetIntegrationTimeFMux(1,fCCDTime)
    res = pDll.JF_USB_GetWaveDataMux(1,fStartWave,fEndWave,fInterval,fWaveData)
    if max(fWaveData)>=65535*0.9:
        print("Integration time too large, current peak value is:",int(max(fWaveData)))
        v1=input("Please re-enter the integration time, or enter q to quit\n")
        if v1=='q':
            break
        else:
            fCCDTime=c_float(float(v1))
    elif max(fWaveData)<65535*0.1:
        print("Integration time too small, current peak value is:",int(max(fWaveData)))
        v1=input("Please re-enter the integration time, or enter q to quit\n")
        if v1=='q':
            break
        else:
            fCCDTime=c_float(float(v1))
        break
    else:
        input("Optimal integration time found, press any key to continue\n")
        break



# Turn off light and subtract zero (dark calibration)
# Dark calibration ("zero") — with the light off, it records baseline sensor noise to subtract later.
input("Make sure the light is off for a dark background, then press any key to start zero calibration\n")
INPUT=c_float*2048
fZero=INPUT()
res=pDll.JF_USB_GetZerolineMux(1,fZero)
if res==1:
    print("Dark zero calibration successful")
for i in range(2048):
    print("{}:{}".format(i,fZero[i]))

# Take reference
# Reference scan ("baseline") — measures a known reference so the sample can be compared against it.
input("Make sure the reference is in place, then press any key to start\n")
INPUT=c_float*2048 
fBase=INPUT()
res=pDll.JF_USB_GetBaselineMux(1,fBase)
if res==1:
    print("Reference measurement successful")
for i in range(2048):
    print("{}:{}".format(i,fBase[i]))


# Start testing, it scans 350–950 nm in 1 nm steps (601 points)
fStartWave=c_float(350)
fEndWave=c_float(950)
fInterval=c_float(1)
iState = input("After placing the sample, enter 1 for absorbance test, 2 for reflectivity test, 3 for transmittance test\n")
if iState=='1':
    # Get absorbance data
    INPUT=c_float*2048
    fAbsorbanceData=INPUT()
    res=pDll.JF_USB_GetAbsorbanceMux(1,fStartWave,fEndWave,fInterval,fAbsorbanceData)
    for i in range(601):
        start_wave_value = float(fStartWave.value)
        interval_value = float(fInterval.value)
        calculated_value = start_wave_value + i * interval_value
        print("{}:{}".format(calculated_value,fAbsorbanceData[i]))
        # Save csv file
        with open("TRAfile.csv","w",encoding="utf-8",newline="") as f:
            csv_writer=csv.writer(f)
            value=[]
            for i in range(601):
                value.clear()
                value.append(i+1)
                value.append(fAbsorbanceData[i])
                csv_writer.writerow(value)
            f.close()
elif iState=='2':
    # Get reflectivity data
    INPUT=c_float*2048
    fReflectData=INPUT()
    res=pDll.JF_USB_GetReflectivityMux(1, fStartWave, fEndWave,fInterval,fReflectData)
    for i in range(601):
        start_wave_value = float(fStartWave.value)
        interval_value = float(fInterval.value)
        calculated_value = start_wave_value + i * interval_value
        print("{}:{}".format(calculated_value,fReflectData[i]))
        # Save csv file
        with open("TRAfile.csv","w",encoding="utf-8",newline="") as f:
            csv_writer=csv.writer(f)
            value=[]
            for i in range(601):
                value.clear()
                value.append(i+1)
                value.append(fReflectData[i])
                csv_writer.writerow(value)
            f.close()
elif iState=='3':
    # Get transmittance data
    INPUT=c_float*2048
    fTransmittance=INPUT()
    res=pDll.JF_USB_GetTransmittanceMux(1, fStartWave, fEndWave,fInterval,fTransmittance)
    for i in range(601):
        start_wave_value = float(fStartWave.value)
        interval_value = float(fInterval.value)
        calculated_value = start_wave_value + i * interval_value
        print("{}:{}".format(calculated_value,fTransmittance[i]))
        # Save csv file
        with open("TRAfile.csv","w",encoding="utf-8",newline="") as f:
            csv_writer=csv.writer(f)
            value=[]
            for i in range(601):
                value.clear()
                value.append(i+1)
                value.append(fTransmittance[i])
                csv_writer.writerow(value)
            f.close()

    
    
