"""
UI class for converting raw data from different echosounders to netcdf or zarr.
"""
import os
import shutil
import xarray as xr
import netCDF4
import numpy as np
import dask
from .convertbase_new import ParseEK60, ParseEK80, ParseAZFP
from .utils.setgroups_new import SetGroupsEK60, SetGroupsEK80, SetGroupsAZFP


class Convert:
    """UI class for using convert objects.

    Sample use case:
        ec = echopype.Convert()

        # set source files
        ec.set_source(
            files=[FILE1, FILE2, FILE3],  # file or list of files
            model='EK80',       # echosounder model
            # xml_path='ABC.xml'  # optional, for AZFP only
            )

        # set parameters that may not already in source files
        ec.set_param({
            'platform_name': 'OOI',
            'platform_type': 'mooring'
            })

        # convert to netcdf, do not combine files, save to source path
        ec.to_netcdf()

        # convert to zarr, combine files, save to s3 bucket
        ec.to_netcdf(combine_opt=True, save_path='s3://AB/CDE')

        # get GPS info only (EK60, EK80)
        ec.to_netcdf(data_type='GPS')

        # get configuration XML only (EK80)
        ec.to_netcdf(data_type='CONFIG_XML')

        # get environment XML only (EK80)
        ec.to_netcdf(data_type='ENV_XML')
    """
    def __init__(self):
        # Attributes
        self.sonar_model = None     # type of echosounder
        self.xml_path = ''          # path to xml file (AZFP only)
                                    # users will get an error if try to set this directly for EK60 or EK80 data
        self.source_file = None     # input file path or list of input file paths
        self.output_file = None     # converted file path or list of converted file paths
        self.extra_files = []       # additional files created when setting groups (EK80 only)
        self._source_path = None    # for convenience only, the path is included in source_file already;
                                    # user should not interact with this directly
        self._output_path = None    # for convenience only, the path is included in source_file already;
                                    # user should not interact with this directly
        self._conversion_params = {}    # a dictionary of conversion parameters,
                                        # the keys could be different for different echosounders.
                                        # This dictionary is set by the `set_param` method.
        self.data_type = 'all'      # type of data to be converted into netcdf or zarr.
                                # - default to 'all'
                                # - 'GPS' are valid for EK60 and EK80 to indicate only GPS related data
                                #   (lat/lon and roll/heave/pitch) are exported.
                                # - 'XML' is valid for EK80 data only to indicate when only the XML
                                #   condiguration header is exported.
        self.combine = False
        self.compress = True
        self.overwrite = False
        self.timestamp_pattern = ''  # regex pattern for timestamp encoded in filename
        self.nmea_gps_sentence = 'GGA'  # select GPS datagram in _set_platform_dict(), default to 'GGA'
        self.set_param({})      # Initialize parameters with empty strings

    def set_source(self, file, model, xml_path=None):
        """Set source file and echosounder model.
        """
        # Check if specified model is valid
        if model == "AZFP":
            ext = '.01A'
            # Check for the xml file if dealing with an AZFP
            if xml_path:
                if '.XML' in xml_path.upper():
                    if not os.path.isfile(xml_path):
                        raise FileNotFoundError(f"There is no file named {os.path.basename(xml_path)}")
                else:
                    raise ValueError(f"{os.path.basename(xml_path)} is not an XML file")
                self.xml_path = xml_path
            else:
                raise ValueError("XML file is required for AZFP raw data")
        elif model == 'EK60' or model == 'EK80':
            ext = '.raw'
        else:
            raise ValueError(model + " is not a supported echosounder model")

        self.sonar_model = model

        # Check if given files are valid
        if isinstance(file, str):
            file = [file]
        try:
            for p in file:
                if not os.path.isfile(p):
                    raise FileNotFoundError(f"There is no file named {os.path.basename(p)}")
                if os.path.splitext(p)[1] != ext:
                    raise ValueError("Not all files are in the same format.")
        except TypeError:
            raise ValueError("file must be string or list-like")

        self.source_file = file

    def set_param(self, param_dict):
        """Allow users to set, ``platform_name``, ``platform_type``, ``platform_code_ICES``, ``water_level``,
        and ```survey_name`` to be saved during the conversion. Extra values are saved to the top level.
        """
        # Platform
        self._conversion_params['platform_name'] = param_dict.get('platform_name', '')
        self._conversion_params['platform_code_ICES'] = param_dict.get('platform_code_ICES', '')
        self._conversion_params['platform_type'] = param_dict.get('platform_type', '')
        self._conversion_params['water_level'] = param_dict.get('water_level', None)
        # Top level
        self._conversion_params['survey_name'] = param_dict.get('survey_name', '')
        for k, v in param_dict.items():
            if k not in self._conversion_params:
                self._conversion_params[k] = v

    def _validate_path(self, file_format, save_path=None):
        """Assemble output file names and path.

        Parameters
        ----------
        save_path : str
            Either a directory or a file. If none then the save path is the same as the raw file.
        file_format : str            .nc or .zarr
        """

        # Raise error if output format is not .nc or .zarr
        if file_format != '.nc' and file_format != '.zarr':
            raise ValueError("File format is not .nc or .zarr")

        filenames = self.source_file

        # Default output directory taken from first input file
        self.out_dir = os.path.dirname(filenames[0])
        if save_path is not None:
            path_ext = os.path.splitext(save_path)[1]
            # Check if save_path is a file or a directory
            if path_ext == '':   # if a directory
                self.out_dir = save_path
            elif (path_ext == '.nc' or path_ext == '.zarr') and len(filenames) == 1:
                self.out_dir = os.path.dirname(save_path)
            else:  # if a file
                raise ValueError("save_path must be a directory")

        # Create folder if save_path does not exist already
        if not os.path.exists(self.out_dir):
            try:
                os.mkdir(self.out_dir)
            # Raise error if save_path is not a folder
            except FileNotFoundError:
                raise ValueError("A valid save directory was not given.")

        # Store output filenames
        files = [os.path.splitext(os.path.basename(f))[0] for f in filenames]
        self.output_file = [os.path.join(self.out_dir, f + file_format) for f in files]
        self.nc_path = [os.path.join(self.out_dir, f + '.nc') for f in files]
        self.zarr_path = [os.path.join(self.out_dir, f + '.zarr') for f in files]

    def _convert_indiv_file(self, file, output_path, save_ext):
        """Convert a single file.
        """
        # use echosounder-specific object
        if self.sonar_model == 'EK60':
            c = ParseEK60
            sg = SetGroupsEK60
        elif self.sonar_model == 'EK80':
            c = ParseEK80
            sg = SetGroupsEK80
        elif self.sonar_model == 'AZFP':
            c = ParseAZFP
            sg = SetGroupsAZFP
        else:
            raise ValueError("Unknown sonar model", self.sonar_model)

        # Check if file exists
        if os.path.exists(output_path) and self.overwrite:
            # Remove the file if self.overwrite is true
            print("          overwriting: " + output_path)
            self._remove(output_path)
        if os.path.exists(output_path):
            # Otherwise, skip saving
            print(f'          ... this file has already been converted to {save_ext}, conversion not executed.')
        else:
            c = c(file)
            c.parse_raw()
            sg = sg(c, input_file=file, output_path=output_path, save_ext=save_ext, compress=self.compress,
                    overwrite=self.overwrite, params=self._conversion_params, extra_files=self.extra_files)
            sg.save()

    def _check_param_consistency(self):
        """Check consistency of key params so that xr.open_mfdataset() will work.
        """
        # TODO: need to figure out exactly what parameters to check.
        #  These will be different for each echosounder model.
        #  Can think about using something like
        #  _check_tx_param_uniqueness() or _check_env_param_uniqueness() for EK60/EK80,
        #  and _check_uniqueness() for AZFP.
        # if self.sonar_model == 'EK60':
        #     pass
        # elif self.sonar_model == 'EK80':
        #     pass
        # elif self.sonar_model == 'AZFP':
        #     parser._check_uniqueness()
        return True

    @staticmethod
    def _remove(path):
        fname, ext = os.path.splitext(path)
        if ext == '.zarr':
            shutil.rmtree(path)
        else:
            os.remove(path)

    def combine_files(self, src_files=None, save_path=None, remove_orig=True):
        """Combine output files when self.combine=True.
        """
        if len(self.source_file) < 2:
            print("Combination did not occur as there is only 1 source file")
            return False
        if not self._check_param_consistency():
            print("Combination did not occur as there are inconsistent parameters")
            return False

        def set_open_dataset(ext):
            if ext == '.nc':
                return xr.open_dataset
            elif ext == '.zarr':
                return xr.open_zarr

        def set_open_mfdataset(ext):
            if ext == '.nc':
                return xr.open_mfdataset
            elif ext == '.zarr':
                return open_mfzarr

        def open_mfzarr(files, group, combine='by_coords', data_vars=None):
            def modify(task):
                return task
            # this is basically what open_mfdataset does
            open_kwargs = dict(decode_cf=True, decode_times=False)
            open_tasks = [dask.delayed(xr.open_zarr)(f, **open_kwargs) for f in files]
            tasks = [dask.delayed(modify)(task) for task in open_tasks]
            datasets = dask.compute(tasks)  # get a list of xarray.Datasets
            combined = xr.combine_nested(datasets)  # or some combination of concat, merge
            return combined

        def _save(ext, ds, path, mode, group=None):
            if ext == '.nc':
                ds.to_netcdf(path=path, mode=mode, group=group)
            else:
                ds.to_zarr(store=path, mode=mode, group=group)

        def copy_vendor(src_file, trg_file):
            # Utility function for copying the filter coefficients from one file into another
            src = netCDF4.Dataset(src_file)
            trg = netCDF4.Dataset(trg_file, mode='a')
            ds_vend = src.groups['Vendor']
            vdr = trg.createGroup('Vendor')
            complex64 = np.dtype([("real", np.float32), ("imag", np.float32)])
            complex64_t = vdr.createCompoundType(complex64, "complex64")

            # set decimation values
            vdr.setncatts({a: ds_vend.getncattr(a) for a in ds_vend.ncattrs()})

            # Create the dimensions of the file
            for k, v in ds_vend.dimensions.items():
                vdr.createDimension(k, len(v) if not v.isunlimited() else None)

            # Create the variables in the file
            for k, v in ds_vend.variables.items():
                data = np.empty(len(v), complex64)
                var = vdr.createVariable(k, complex64_t, v.dimensions)
                var[:] = data

            src.close()
            trg.close()

        def split_into_groups(files):
            if self.sonar_model == 'EK80':
                file_groups = [[], []]
                for f in files:
                    if '_cw' in f:
                        file_groups[1].append(f)
                    else:
                        file_groups[0].append(f)
            else:
                file_groups = [files]
            return file_groups

        print('combining files...')
        src_files = self.output_file if src_files is None else src_files
        ext = '.nc'
        if self.sonar_model == 'EK80':
            file_groups = split_into_groups(src_files + self.extra_files)
        if save_path is None:
            fname, ext = os.path.splitext(src_files[0])
            save_path = fname + '[combined]' + ext
        elif isinstance(save_path, str):
            fname, ext = os.path.splitext(save_path)
            # If save_path is a directory. (It must exist due to validate_path)
            if ext == '':
                file = os.path.basename(src_files[0])
                fname, ext = os.path.splitext(file)
                save_path = os.path.join(save_path, fname + '[combined]' + ext)
        else:
            raise ValueError("Invalid save path")

        _open_dataset = set_open_dataset(ext)
        _open_mfdataset = set_open_mfdataset(ext)
        for i, file_group in enumerate(file_groups):
            # Append '_cw' to EK80 filepath if combining CW files
            if i == 1:
                fname, ext = os.path.splitext(save_path)
                save_path = fname + '_cw' + ext
            # Open multiple files as one dataset of each group and save them into a single file
            # Combine Top-level
            with _open_dataset(file_group[0]) as ds_top:
                _save(ext, ds_top, save_path, 'w')
            # Combine Provenance
            with _open_dataset(file_group[0], group='Provenance') as ds_prov:
                _save(ext, ds_prov, save_path, 'a', group='Provenance')
            # Combine Sonar
            with _open_dataset(file_group[0], group='Sonar') as ds_sonar:
                _save(ext, ds_sonar, save_path, 'a', group='Sonar')
            # Combine Beam
            with _open_mfdataset(file_group, group='Beam', combine='by_coords', data_vars='minimal') as ds_beam:
                _save(ext, ds_beam, save_path, 'a', group='Beam')
            # Combine Environment
            # AZFP environment changes as a function of ping time
            if self.sonar_model == 'AZFP':
                with _open_mfdataset(file_group, group='Environment', combine='by_coords') as ds_env:
                    _save(ext, ds_env, save_path, 'a', group='Environment')
            else:
                with _open_dataset(file_group[0], group='Environment') as ds_env:
                    _save(ext, ds_env, save_path, 'a', group='Environment')
            # Combine Platfrom
            # The platform group for AZFP does not have coordinates, so it must be handled differently from EK60
            if self.sonar_model == 'AZFP':
                with _open_dataset(file_group[0], group='Platform') as ds_plat:
                    _save(ext, ds_plat, save_path, 'a', group='Platform')
            else:
                with _open_mfdataset(file_group, group='Platform', combine='by_coords') as ds_plat:
                    _save(ext, ds_plat, save_path, 'a', group='Platform')
            # Combine Sonar-specific
            if self.sonar_model == 'AZFP':
                # EK60 does not have the "vendor specific" group
                with _open_mfdataset(file_group, group='Vendor', combine='by_coords', data_vars='minimal') as ds_vend:
                    _save(ext, ds_vend, save_path, 'a', group='Vendor')
            if self.sonar_model == 'EK80' or self.sonar_model == 'EK60':
                # AZFP does not record NMEA data
                with _open_mfdataset(file_group, group='Platform/NMEA',
                                     combine='nested', concat_dim='time', decode_times=False) as ds_nmea:
                    _save(ext, ds_nmea, save_path, 'a', group='Platform/NMEA')
            if self.sonar_model == 'EK80':
                # Save filter coefficients in EK80
                copy_vendor(file_group[0], save_path)

        # Delete files after combining
        if remove_orig:
            for f in src_files + self.extra_files:
                self._remove(f)
        return True

    def to_netcdf(self, save_path=None, data_type='all', compress=True, overwrite=True, combine=False, parallel=False):
        """Convert a file or a list of files to netcdf format.
        """
        self.data_type = data_type
        self.compress = compress
        self.combine = combine
        self.overwrite = overwrite

        self._validate_path('.nc', save_path)
        # Sequential or parallel conversion
        if not parallel:
            for i, file in enumerate(self.source_file):
                # convert file one by one into path set by validate_path()
                self._convert_indiv_file(file=file, output_path=self.output_file[i], save_ext='.nc')
        # else:
            # use dask syntax but we'll probably use something else, like multiprocessing?
            # delayed(self._convert_indiv_file(file=file, path=save_path, output_format='netcdf'))

        # combine files if needed
        if self.combine:
            self.combine_files(save_path=save_path, remove_orig=True)

    def to_zarr(self, save_path=None, data_type='all', compress=True, combine=False, overwrite=False, parallel=False):
        """Convert a file or a list of files to zarr format.
        """
        self.data_type = data_type
        self.compress = compress
        self.combine = combine
        self.overwrite = overwrite

        self._validate_path('.zarr', save_path)
        # Sequential or parallel conversion
        if not parallel:
            for i, file in enumerate(self.source_file):
                # convert file one by one into path set by validate_path()
                self._convert_indiv_file(file=file, output_path=self.output_file[i], save_ext='.zarr')
        # else:
            # use dask syntax but we'll probably use something else, like multiprocessing?
            # delayed(self._convert_indiv_file(file=file, path=save_path, output_format='netcdf'))

        # combine files if needed
        if self.combine:
            self.combine_files(save_path=save_path, remove_orig=True)
