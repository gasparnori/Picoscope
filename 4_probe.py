from deviceControl_4probe import MultiprocConnector, DevControl, dataBuff, NUM_SAMPLES, NANO2MILI
import matplotlib.pyplot as plt
import multiprocessing as mp
import sys
from datetime import datetime
import csv

#if False, it uses the settings of the IVS computer
Debugging=False

if not Debugging:
###################### defining paths on the experiment computer ###########
    saveOutput=True
    filePath='C:/GluSense/Calibration/' #where to save the output
    logPath = "C:/GluSense/GluSense Monitoring Software/Log/InVitroApp.txt" #where to find the log
    ### 5 minutes= 300 seconds = 300000 mseconds
    Measurement_Rate=30000 ## in mseconds
else:
###################### defining paths for debugging ###########
    saveOutput=False
    filePath = '' #where to save the output
    logPath = "InVitroApp.txt" ##where to find the log
    Measurement_Rate = 2000

default_temp=33.0
default_conc=400.0

temperature_coeff=0.0162
evaporation_coeff=0.0000205650

##error thresholds
# highest standard deviation that we still tolerate
STDthres=1500
# if the volume is smaller than the threshold it must be leaking or be in the middle of liquid change
VOLthres=30
# the last time the IVS logged volume and concentration (not important for now)
TIMEthres=10000000
#output fields of the output CSV file
outputFields=['concentration [mg/dL]', 'target concentration [mg/dL]', 'volume [mL]',  'temperature [Celsius]',
              'V2 mean [mV]', 'V2 std [uV]', 'I mean [mA]',
              'I std [uA]', 'conductivity [mS]','compensated conductivity [mS]', 'timestamp']

class measurement:
    '''a class for one measurement point'''
    def __init__(self, Vavg, Vstd, Iavg, Istd):
        self.timestamp=datetime.today()
        self.timestring=datetime.strftime(self.timestamp, '%d/%m/%Y %H:%M')
        self.Vrms=Vavg # Average voltage measured from Channel A on Picoscope
        self.Vstd=Vstd # standard deviation of the above
        self.Irms=Iavg # average current measured through Channel B
        self.Istd = Istd
        self.conductivity=Iavg/Vavg*1000 # in mSiemens

    def formatOutput(self):
       return {'concentration [mg/dL]': self.concentration,
         'target concentration [mg/dL]': self.theoretical_conc,
         'volume [mL]': self.volume,
         'temperature [Celsius]':self.temperature,
         'V2 mean [mV]': self.Vrms,
         'V2 std [uV]': self.Vstd,
         'I mean [mA]': self.Irms,
         'I std [uA]': self.Istd,
         'conductivity [mS]': self.conductivity,
         'compensated conductivity [mS]': self.conductivity_compensated,
         'timestamp': self.timestring}

    def getLog(self):
        '''open log file and gets the last volume and concentration'''
        try:
            with open(logPath, 'r', encoding='utf8') as f:
                f = f.readlines()
                for line in f:
                    if "Current concentration" in line:
                        lastconcentration = line
                    if "Current volume" in line:
                        lastvolume = line
                    if "Temperature step function" in line:
                        lasttemperature=line
                    if 'Concentration step change to ' in line:
                        conc_theoretical=line

            #checking if the timestamps are close enough
            date = datetime.strptime(lastconcentration.split(' [')[0], '%d/%m/%Y %H:%M')
            tdiff=int(abs((date - self.timestamp).total_seconds()) / 60) #in minutes
            if tdiff > TIMEthres:#the timestamp is too old
                print ("Too old concentration values...")
                c='0'
                v='0'
                self.concentration = 0
                self.volume=0
                self.temperature = 0
                self.theoretical_conc=0
            else:
                c = lastconcentration.split('Current concentration')[1].split('\n')[0]
                self.concentration = float(c) if c is not None else None
                v=lastvolume.split('Current volume')[1].split('\n')[0]
                self.volume = float(v) if v is not None else None
                t = lasttemperature.split('to ')[1].split('degrees')[0]
                self.temperature=  float(t) if t is not None else None
                t_c=conc_theoretical.split('Concentration step change to ')[1].split(' in')[0]
                self.theoretical_conc=float(t_c) if t_c is not None else None
        except:
            #if another exception occured (for example the volume was not readable)
            self.concentration=-1
            self.volume=-1
            self.temperature= -1
            self.theoretical_conc = -1
        print("measurement at " + self.timestring + " \n\t",
              "concentration: {:.2f} ml\n\t".format(self.concentration),
              "temperature: {:.1f}\n\t".format(self.temperature),
              "volume: {:.2f} ml\n\t".format(self.volume),
              " V2: {:.3f} mV\n\t".format(self.Vrms),
              " I: {:.3f} mA\n\t".format(self.Irms),
              "G: {:.3f} mS ".format(self.conductivity))
    def compensate(self, t0):
        self.compensateEvaporation(t0)
        self.compensateTemperature()
    def compensateEvaporation(self, t0):
        timedelay = (self.timestamp - t0).total_seconds() / 60
        self.conductivity_compensated = self.conductivity/ (1 + evaporation_coeff * timedelay)
        return
    def compensateTemperature(self):
        self.conductivity_compensated = self.conductivity_compensated / \
                                        (1 + temperature_coeff * (self.temperature - default_temp))
        return

def initOutput(filename):
    '''initializing the output csv file'''
    with open(filename, mode='w',  newline='') as results:
        writer = csv.DictWriter(results, fieldnames=outputFields)
        writer.writeheader()

def appendRow(filename, m):
    ''' Opens the output csv, append a row to it, and closes it'''
    with open(filename, mode='a', newline='') as results:
        writer = csv.DictWriter(results, fieldnames=outputFields)
        writer.writerow(m.formatOutput())

def main():
    ''' main function '''
    counter = Measurement_Rate #counts down to 0
    try:
        filename = datetime.strftime(datetime.now(), filePath + 'Picoresults_%Y_%m_%d_%H_%M.csv')
        if saveOutput:
            initOutput(filename)
        #connecting to the GUI to see the graph in real time
        connector = MultiprocConnector()
        #initializing the device
        dev = DevControl()
        #stats is the buffer that stores the actual measurements
        stats = dataBuff()
        #important for evaporation compensation
        t0=datetime.now()
        current_conc=default_conc

        while True:
            #reads data from the oscilloscope
            #channel A: Voltage
            #channel B: current
            #bufA, bufB: wave values-->used for plotting
            time, bufA, bufB = dev.getData()
            stats.addMeasurement(bufA, bufB)

            #update the plot with the new data
            connector.updateData(time, bufA, bufB)
            ##counting back for 5 minutes
            counter=counter - dev.timeInterval.value * NUM_SAMPLES / NANO2MILI

            if counter<1:
                ## gets averages and standard deviations
                Vavg, Vstd, Iavg, Istd=stats.getStats()
                ## format the measurement point into one class
                m=measurement(Vavg, Vstd, Iavg, Istd)
                ## adds volume and concentration values from the log
                m.getLog()
                ##if liquid was replaced, evaporation resets
                if current_conc != m.concentration:
                    current_conc==m.concentration
                    t0= m.timestamp
                m.compensate(t0)
                #if the measurement point was valid, it saves it in the CSV file
                if m.concentration is not None and m.volume is not None:
                    if m.volume >= VOLthres and m.Vstd < STDthres and m.Istd < STDthres:
                        if saveOutput:
                            appendRow(filename, m)
                        # resets counter
                        counter = Measurement_Rate

    except KeyboardInterrupt:
        print('Interrupted')
        connector.sendFinished()
        dev.closeDevice()
        sys.exit(1)

    plt.show()
if __name__ == '__main__':
    main()

