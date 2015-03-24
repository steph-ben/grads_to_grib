#!/usr/bin/python
"""
Convert WaveWatch3 model from Grads to GRIB

Needed tools:
    - Grads
    - CDO https://code.zmaw.de/projects/cdo/files
    - GRIB API https://software.ecmwf.int/wiki/display/GRIB/Home

It needs input files:
    - grd2.grads : binary grads data
    - grd2.ctl : "index" file describing the data
    - grads_nc : grads script to convert data to NetCDF

The grads_nc file looks like (for each parameter):
    #surface wind 10m direction (unit=degree)
    'open grd2.ctl'
    'set t 1 last'
    'dir=57.324*ATAN2(wv,wu)+180'
    'dirw=dir'
    'define dirw=dirw'
    'set sdfwrite -flt windir.nc'
    'sdfwrite dirw'
    'reinit'
    ...

The grd2.ctl file looks like:
    DSET      grd2.grads
    TITLE     WAVEWATCH III gridded data
    OPTIONS   sequential
    UNDEF      -999.9
    XDEF      481  LINEAR     90.00000     0.12500
    YDEF      241  LINEAR    -15.00000     0.12500
    ZDEF        1  LINEAR   1000.00000     1.00000
    TDEF       57  LINEAR     00:00Z05FEB2015   3HR
    VARS       33
    MAP     0  99  grid use map
    DEPTH   0  99  Water depth
    CX      0  99  Current U (m/s)
    CY      0  99  Current V (m/s)
    WU      0  99  Wind U (m/s)
    ...
"""
import os
import sys
import shutil
import datetime
import ftplib
import shlex
import subprocess
import glob
from distutils.spawn import find_executable


BASEDIR = os.path.dirname(__file__)
NETCDF_OUTDIR = os.path.join(BASEDIR, 'netcdf')
TMP_DIR = os.path.join(BASEDIR, 'tmp')
GRIB_OUTDIR = os.path.join(BASEDIR, 'grib')


#
# Required external executables
#
GRADS_BIN = 'grads'
CDO_BIN = 'cdo'
GRIB_SET_BIN = 'grib_set'

if not find_executable(GRADS_BIN): sys.exit('ERROR: Unable to find %s' % GRADS_BIN)
if not find_executable(CDO_BIN): sys.exit('ERROR: Unable to find %s' % CDO_BIN)
if not find_executable(GRIB_SET_BIN): sys.exit('ERROR: Unable to find %s' % GRIB_SET_BIN)


#
# WW3 parameters and GRIB codes
#
MAX_FORECAST_RANGE = 168
FORECAST_RANGE_STEP = 3
WW3_GRIB_CODES = {
    'table2Version': 2,
    'centre': 195,
    'subCentre': 0,
    'generatingProcessIdentifier': 201,
}
PARAMETERS_CODES = {
    'waveheight': 
        {'indicatorOfParameter': 105},
    'wavedir': 
        {'indicatorOfParameter': 200},
    'waveperiod': 
        {'indicatorOfParameter': 201},
    'windsea': 
        {'indicatorOfParameter': 100},
    'windseadir': 
        {'indicatorOfParameter': 101},
    'windseaper': 
        {'indicatorOfParameter': 110},
    'primaryswell': 
        {'indicatorOfParameter': 202},
    'primaryswelldir': 
        {'indicatorOfParameter': 107},
    'primaryswellperiod': 
        {'indicatorOfParameter': 108},
    'secondaryswell': 
        {'indicatorOfParameter': 203},
    'secondaryswelldir': 
        {'indicatorOfParameter': 109},
    'secondaryswellperiod': 
        {'indicatorOfParameter': 110},
    'windu': 
        {'indicatorOfParameter': 33, 'indicatorOfTypeOfLevel': '105', 'level':10},
    'windv': 
        {'indicatorOfParameter': 34, 'indicatorOfTypeOfLevel': '105', 'level':10},
}

#
# To send files to Transmet
#
TRANSMET_HOST = "172.19.22.69"
TRANSMET_USER = "ww3ani"
TRANSMET_PASS = "STRcb56p"


class GradsToGrib():
    def run(self):
        g.clean_directories()
        g.grads_to_netcdf()
        g.netcdf_to_grib()
        #g.run_timestamp = datetime.datetime.strptime('2015-02-05T00:00:00', '%Y-%m-%dT%H:%M:%S')
        g.merge_per_forecastrange()

    def clean_directories(self):
        for d in TMP_DIR, NETCDF_OUTDIR, GRIB_OUTDIR:
            if not os.path.isdir(d):
                os.mkdir(d)
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))

    def grads_to_netcdf(self):
        """
        Convert grads file to NetCDF using Grads config file
        Output will be one file per parameter

        Note that all codes will be empty (parameters, levels, etc)
        """
        print " *** Converting Grads to NetCDF ..."
        run('%s -blc "run grads_nc"' % GRADS_BIN)

        for nc_file in os.listdir('.'):
            if nc_file[-3:] == '.nc':
                shutil.move(nc_file, NETCDF_OUTDIR)

        print "\n\n *** NetCDF files:"
        for f in os.listdir(NETCDF_OUTDIR):
            print f

    def netcdf_to_grib(self):
        """
        It does the following:
            - convert NetCDF file to Grib using cdo tools
            - set correct grib codes according to the filename, using grib_api
        """
        self.run_timestamp = None

        for nc_file in os.listdir(NETCDF_OUTDIR):
            grib_file = os.path.join(TMP_DIR, nc_file.replace('.nc', '.grb'))
            nc_file = os.path.join(NETCDF_OUTDIR, nc_file)

            print "\n *** Converting %s to %s ..." % (nc_file, grib_file)
            cmd = "%s -f grb copy %s %s" % (CDO_BIN, nc_file, grib_file)
            run(cmd)

            if self.run_timestamp is None:
                #
                # Getting run date by the lowest timestamp
                #
                r, timestamp_list, stderr = run('%s showtimestamp %s' % (CDO_BIN, grib_file))
                s = timestamp_list.split('  ')[1]
                self.run_timestamp = datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')

            #
            # Set grib codes for the whole file
            #
            param_name, ext = os.path.splitext(os.path.basename(nc_file))
            if param_name not in PARAMETERS_CODES:
                print('WARNING: GRIB code for parameters %s not defined' % param_name)
                continue
            param_codes = PARAMETERS_CODES[param_name]
            grib_codes = WW3_GRIB_CODES
            grib_codes.update(param_codes)
            self.set_grib_codes(grib_file, grib_codes)

            #
            # Split grib file per forecast range and set correct datetime grib codes
            #
            self.split_per_forecastrange(grib_file, self.run_timestamp, param_name)

    def set_grib_codes(self, grib_file, grib_codes):
        grib_tmp_file = '%s.tmp' % grib_file
        args = ["%s=%s" % (k, v) for k, v, in grib_codes.items()]
        all = ','.join(args)

        cmd = "%s -s %s %s %s" % (GRIB_SET_BIN, all, grib_file, grib_tmp_file)
        run(cmd)
        shutil.move(grib_tmp_file, grib_file)

    def split_per_forecastrange(self, grib_file, run_timestamp, param_name):
        for forecastrange in range(0, MAX_FORECAST_RANGE+1, FORECAST_RANGE_STEP):
            d = run_timestamp + datetime.timedelta(hours=forecastrange)

            # Codes in original file
            o_datadate = d.strftime('%Y%m%d')
            o_datatime = d.strftime('%H00')

            # Codes for target file
            t_datadate = run_timestamp.strftime('%Y%m%d')
            t_datatime = run_timestamp.strftime('%H00')
            t_stepRange = forecastrange

            filename_forecastrange = os.path.join(TMP_DIR,
                "ww3.%s.%s.%s.grb" % (param_name, run_timestamp.strftime('%Y%m%d%H'), str(forecastrange).zfill(3))
            )
            cmd = GRIB_SET_BIN
            if forecastrange == 3:
                # Grads/cdo fill stepRange only for fcstRange=3, don't ask why ...
                cmd += " -S -w dataDate=%s,dataTime=%s,stepRange=3" % (o_datadate, t_datatime)
                cmd += " -s dataDate=%s,dataTime=%s,stepRange=%s" % (t_datadate, t_datatime, t_stepRange)
            else:
                cmd += " -S -w dataDate=%s,dataTime=%s" % (o_datadate, o_datatime)
                cmd += " -s dataDate=%s,dataTime=%s,stepRange=%s" % (t_datadate, t_datatime, t_stepRange)
            cmd += " %s %s" % (grib_file, filename_forecastrange)
            run(cmd)

    def merge_per_forecastrange(self):
        for forecastrange in range(0, MAX_FORECAST_RANGE+1, FORECAST_RANGE_STEP):
            output_file = os.path.join(GRIB_OUTDIR, 'ww3.%s.%s.grb' % (self.run_timestamp.strftime('%Y%m%d%H'), str(forecastrange).zfill(3)))
            wd = os.path.join(TMP_DIR, "ww3.*.*.%s.grb" % str(forecastrange).zfill(3))

            print " *** Generating full forecastrange %s: %s" % (output_file, wd)
            fd = open(output_file, 'w')
            try:
                for grib_file in glob.glob(wd):
                    pd = open(grib_file)
                    try:
                        for l in pd:
                            fd.write(l)
                    finally:
                        pd.close()
            finally:
                fd.close()

            #
            # Send all results to Transmet
            #
            self.send_to_transmet(output_file, forecastrange)

    def send_to_transmet(self, filename, forecastrange):
        """
        T_YXWR45_C_AWFA_20150323033956_wrfprs_d01_45_grb_11071.bin.tmp
        """
        destination_filename = self.get_header_from_filename(filename, forecastrange)

        print " *** Sending to FTP %s : %s" % (TRANSMET_HOST, destination_filename)
        log = ""
        ftp = ftplib.FTP(TRANSMET_HOST, TRANSMET_USER, TRANSMET_PASS)
        log += ftp.getwelcome() + "\n"
        try:
            ftp.cwd('.')
            log += ftp.storbinary('STOR %s.tmp' % (destination_filename), open(filename, 'rb')) + "\n"
            log += ftp.rename("%s.tmp" % destination_filename, "%s.bin" % destination_filename) + "\n"
        finally:
            log += ftp.quit() + "\n"
        print log

    def get_header_from_filename(self, filename, forecastrange):
        TTA1 = 'YXM'
        CCCC = 'WIIX'
        ii = str(forecastrange % 100).zfill(2)

        if forecastrange < 100:
            A2 = 'R'
        elif forecastrange > 99:
            A2 = 'S'
        elif forecastrange > 199:
            A2 = 'T'

        timestamp = self.run_timestamp.strftime('%Y%m%d%H%M%S')
        f = os.path.basename(filename)
        return 'T_%s%s%s_C_%s_%s_%s' % (TTA1, A2, ii, CCCC, timestamp, f)


def run(cmd):
    #print cmd
    p = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE)
    stdout, stderr = p.communicate()
    return p.returncode, stdout, stderr


if __name__ == "__main__":
    g = GradsToGrib()
    g.run()

