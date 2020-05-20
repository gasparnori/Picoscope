
# Original from Copyright (C) 2016 Pico Technology Ltd.
# Redistribution with or without modification is allowed provided that this copyright notice is preserved.
# modified

import ctypes
import matplotlib.pyplot as plt
from ctypes import *
from picosdk.functions import adc2mV
import numpy as np
import multiprocessing as mp

### Constants ##############
# definitions from ps2000Api.h:
PS2000_BATCH_AND_SERIAL = 4 # ps2000_get_unit_info() info type
PS2000_SINE = 0 # ps2000_set_sig_gen_built_in() wave type
PS2000_CHANNEL_A = 0 # ps2000_set_channel() channel ID
PS2000_CHANNEL_B = 1 # ps2000_set_channel() channel ID
PS2000_NONE = 5 # ps2000_set_trigger() source

#oscilloscope voltage ranges
PS2000_VOLTAGE_RANGE = {
    'PS2000_20MV':  1,
    'PS2000_50MV':  2,
    'PS2000_100MV': 3,
    'PS2000_200MV': 4,
    'PS2000_500MV': 5,
    'PS2000_1V':    6,
    'PS2000_2V':    7,
    'PS2000_5V':    8,
    'PS2000_10V':   9,
    'PS2000_20V':   10,
}

NANO2MILI=1000000
MAX_ADC=32767
NUM_SAMPLES=2000
SIGNAL_AMPL=2000000  #peak to peak amplitude in uV
SIGNAL_FREQ =1000
OUTPUTIMP =600
INPUTIMP=1000000


#if the signal generator amplitude is smaller than 2V, the oscilloscope resolution can be 2V, otherwise it's 5V
if SIGNAL_AMPL<=2000000:
    voltrange=PS2000_VOLTAGE_RANGE['PS2000_2V']
else:
    voltrange=PS2000_VOLTAGE_RANGE['PS2000_5V']

#how many data to save for averaging the results
DATA_BUF=100

###Channel A is Ch1: measurement, voltage should be calculated from here
### Channel B is Ch2 : input, current should be calculated from here
class dataBuff:
    '''a class that stores an array of measurements: LIFO buffer'''
    def __init__(self):
        self.Vlist = np.zeros(DATA_BUF)
        self.Vstd = 0
        self.Vavg=0
        self.Ilist = np.ones(DATA_BUF)
        self.Istd = 0
        self.Iavg=0
        self.num_samples=0

    ## the current drawn from the function generator is:
    #  (output voltage-measured voltage)/output impedance
    ## we are calculating in RMS values!!!
    def calculateCurrent(self, chB):
        Vout = ((SIGNAL_AMPL/2000) /(np.sqrt(2)))
        return ((Vout-chB)/OUTPUTIMP)

    def addMeasurement(self, chA, chB):
        '''adds the rms of the newest measurement to the buffer,
        calculates average and standard deviation'''
        #getting RMS
        Arms = np.sqrt(np.mean(np.power(chA, 2)))
        Brms = np.sqrt(np.mean(np.power(chB, 2)))

        #if the buffer is not full yet:
        if self.num_samples<DATA_BUF:
            self.Vlist[self.num_samples]=Arms
            self.Ilist[self.num_samples]=self.calculateCurrent(Brms)
            self.num_samples = self.num_samples + 1
        #if the buffer is already full, it works as a LIFO
        else:
            self.Vlist[0:-1]=self.Vlist[1:]
            self.Vlist[-1]=Arms
            self.Ilist[0:-1]=self.Ilist[1:]
            self.Ilist[-1]=self.calculateCurrent(Brms)

        self.Vavg=np.mean(self.Vlist[0:self.num_samples])
        self.Vstd=np.std(self.Vlist[0:self.num_samples])
        self.Istd=np.std(self.Ilist[0:self.num_samples])
        self.Iavg=np.mean(self.Ilist[0:self.num_samples])
        return

    def getStats(self):
        # print("V avg:", self.stats.Vavg, "V diff: ", self.stats.Iavg)
        # Mean is in mV, std is in uV so it needs to be multiplied by 1000
        return self.Vavg, self.Vstd * 1000, self.Iavg, self.Istd * 1000

class ScopePlotter(object):
    ''' class to plot the oscilloscope signals in real time'''
    def __init__(self):
        ''' initialize a figure with the two channels '''
        self.f,self.a=plt.subplots(1,1)
        self.f.suptitle("Scope plotter")
        self.aCurrent=self.a.twinx()
        self.a.set_xlabel('Time (ms)')
        self.a.set_ylabel('Voltage (mV)')
        self.aCurrent.set_ylabel('Current [mA]')
        self.chA, = self.a.plot([], [], color='b', linestyle='--', label='Voltage (chA)')
        self.chB, = self.aCurrent.plot([], [], color='y', linestyle='--', label='Current (chB)')
        self.a.legend(loc='upper left')
        self.aCurrent.legend(loc='upper right')

    def updateData(self, time, dataA, dataB):
        ''' plot data from channel A and B'''

        self.chA.set_xdata(time)
        self.chB.set_xdata(time)
        self.chA.set_ydata(dataA)
        self.chB.set_ydata(dataB)

        self.a.set_ylim((min(dataA)-100, max(dataA)+100))
        self.aCurrent.set_ylim((min(dataB)-100, max(dataB)+100))
        self.a.set_xlim(time[0], time[-1])
        return

    def terminate(self):
        plt.close('all')

    def call_back(self):
        '''called at every update '''
        while self.pipe.poll():
            command = self.pipe.recv()
            if command is None:
                self.terminate()
                return False
            else:
                self.updateData(command['time'],command['chA'], command['chB'])
        self.f.canvas.draw()
        return True

    def __call__(self, pipe):
        ''' starting point'''
        print('starting plotter...')
        self.pipe = pipe
        timer = self.f.canvas.new_timer(interval=100)
        timer.add_callback(self.call_back)
        timer.start()
        print('...done')
        plt.show()

class MultiprocConnector(object):
    def __init__(self):
        print ('multiproc created')
        self.plot_pipe, plotter_pipe = mp.Pipe()
        self.plotter = ScopePlotter()
        self.plot_process = mp.Process(
            target=self.plotter, args=(plotter_pipe,), daemon=True)
        self.plot_process.start()

    def sendFinished(self):
        self.plot_pipe.send(None)

    def updateData(self, time, ChA, ChB):
        data = {'chA': ChA, 'chB': ChB, 'time': time}
        self.plot_pipe.send(data)

class DevControl:
    '''device control class '''
    def __init__(self, A_state='on', B_state='on'):
        print('device control created')

        self.picoObj = ctypes.windll.LoadLibrary('PS2000')
        self.device = self.picoObj.ps2000_open_unit()
        p = create_string_buffer(100)

        status = self.picoObj.ps2000_get_unit_info(self.device, p, 100,PS2000_BATCH_AND_SERIAL)
        if status == 0:
            print("Failed to get unit info.\n")
            exit(0)
        else:
            serial_no = p.value.decode()
            print("Device serial no (" + str(status) + ' chars reported): "' + str(serial_no)
                  + '" (' + str(len(serial_no)) + ' chars found).\n')
            self.startDevice(A_state, B_state)

    def startDevice(self, A_state='on', B_state='on'):
        self.initSignalGen()
        self.setCh('A', A_state)
        self.setCh('B', B_state)

    def getData(self):
        self.getBlock()
        #wave values
        self.bufA, self.bufB, self.time = self.retrieveCh()
        return self.time, self.bufA, self.bufB

    def closeDevice(self):
        status = self.picoObj.ps2000_close_unit(self.device)

        if status == 0:
            print("Failed to close unit\n")
        else:
            print("Unit closed\n")

    def initSignalGen(self):# generate a +/- 1 V sine wave
        sg_offset = c_long(0) # offset voltage in microvolts
        sg_pktopk = c_ulong(SIGNAL_AMPL) # peak to peak amplitude in microvolts
        sg_wavetype = PS2000_SINE
        sg_startfreq = c_float(SIGNAL_FREQ) # assuming it's in hertz
        sg_stopfreq = c_float(SIGNAL_FREQ)
        sg_increment = c_float(0) # shouldn't matter if not sweeping
        sg_dwell = c_float(0)
        sg_sweeptype = 0
        sg_sweeps = c_ulong(0)

        status = self.picoObj.ps2000_set_sig_gen_built_in(
            self.device,
            sg_offset,
            sg_pktopk,
            sg_wavetype,
            sg_startfreq,
            sg_stopfreq,
            sg_increment,
            sg_dwell,
            sg_sweeptype,
            sg_sweeps)

        print ("status: ", status)

        if status == 0:
            print("Failed to set up sig. gen.\n")
        else:
            print("Sig. gen. running.\n")

    def setCh(self, name, state):
        ch=PS2000_CHANNEL_A if name=='A' else PS2000_CHANNEL_B
        enable=1 if state=='on' else 0

        status = self.picoObj.ps2000_set_channel(
            self.device, ch,
            enable,  # 1 = enabled
            1,  # 1 = DC, 0 = AC
            voltrange)
        if status == 0:
            print("Failed to set up channel "+name+".\n")
        else:
            print("Channel "+name+" set to +/-2 V DC.\n")
        return

    def setTrigger(self):
        # Set up triggering:
        status = self.picoObj.ps2000_set_trigger(
            self.device, PS2000_NONE,  # no trigger
            0,  # threshold,
            0,  # direction,
            0,  # delay,
            1,  # auto_trigger_ms (set to 1 as a precaution, so scope doesn't hang up)
        )
        if status == 0:
            print("Failed to set up trigger.\n")
        else:
            print("Trigger set to 'none'.\n")

    def getBlock(self):
        # Let's say we want to capture one cycle of our 1 kHz signal with 1000 samples.
        # So we want 1000 samples in 1 ms.
        no_of_samples = c_long(NUM_SAMPLES)
        # That's 1 us per sample, or 1 MS/s.
        # Scope's max. sampling rate is 100 MS/s.
        # Therefore we want 1/100 of max. sampling rate.
        # Timebases go in multiples of 2, so nearest is 1/128 of 100 MS/s = 0.78125 MS/s.
        # 1/128 is timebase 7.
        timebase = c_short(11)
        self.timeInterval = ctypes.c_int32()
        timeUnits = ctypes.c_int32()
        oversample = ctypes.c_int16(1)
        maxSamplesReturn = ctypes.c_int32()
        timeIndisposedms = ctypes.c_int32()
        status = self.picoObj.ps2000_get_timebase(self.device, timebase, no_of_samples.value, ctypes.byref(self.timeInterval), ctypes.byref(timeUnits),
                                 oversample, ctypes.byref(maxSamplesReturn))
        if status == 0:
            print("Failed to get timebase.\n")
        # Expect return value:
        status = self.picoObj.ps2000_run_block(
            self.device,
            no_of_samples,
            timebase,
            1,  # oversample, not used
            byref(timeIndisposedms)
        )

        if status == 0:
            print("Failed to start block mode.\n")
        #else:
        #    print("Block mode started: see you in " + str(timeIndisposedms.value) + " ms.\n")

        status = 0  # intialise to 'not ready'

        while status == 0:  # wait until finished or failed
            status = self.picoObj.ps2000_ready(self.device)

        if status < 0:
            print("USB transfer failed.\n")
        #else:
        #    print("Data captured.\n")
        return

    def retrieveCh(self):
        # Create buffers ready for data
        bufferA = (ctypes.c_int16 * NUM_SAMPLES)()
        bufferB = (ctypes.c_int16 * NUM_SAMPLES)()
        overflow = c_long()
        cmaxSamples = ctypes.c_int32(NUM_SAMPLES)
        status = self.picoObj.ps2000_get_values(self.device, ctypes.byref(bufferA), ctypes.byref(bufferB), None, None,
                                                   ctypes.byref(overflow), cmaxSamples)
        if overflow.value != 0:
            print("Some buffers overflowed: code " + str(overflow.value) + ".\n")

        if status == 0:
            print("Failed to get values.\n")
        #else:
        #    print (".")
            #print("Got " + str(status) + " values.\n")

        # find maximum ADC count value
        maxADC = ctypes.c_int16(MAX_ADC)

        # convert ADC counts data to mV
        adc2mVChA = adc2mV(bufferA, voltrange, maxADC)
        adc2mVChB = adc2mV(bufferB, voltrange, maxADC)

        # Create time data
        time = np.linspace(0, (cmaxSamples.value) * self.timeInterval.value, cmaxSamples.value)/NANO2MILI
        return adc2mVChA, adc2mVChB, time

