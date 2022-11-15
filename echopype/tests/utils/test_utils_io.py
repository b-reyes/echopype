import os
import fsspec
from pathlib import Path
import pytest
from typing import Tuple

from echopype.utils.io import sanitize_file_path, validate_output_path, join_paths


@pytest.mark.parametrize(
    "file_path, should_fail, file_type",
    [
        ('https://example.com/test.nc', True, 'nc'),
        ('https://example.com/test.zarr', False, 'zarr'),
        ('folder/test.nc', False, 'nc'),
        ('folder/test.zarr', False, 'zarr'),
        (Path('https:/example.com/test.nc'), True, 'nc'),
        (Path('https:/example.com/test.zarr'), True, 'zarr'),
        (Path('folder/test.nc'), False, 'nc'),
        (Path('folder/test.zarr'), False, 'zarr'),
        (fsspec.get_mapper('https://example.com/test.nc'), True, 'nc'),
        (fsspec.get_mapper('https:/example.com/test.zarr'), False, 'zarr'),
        (fsspec.get_mapper('folder/test.nc'), False, 'nc'),
        (fsspec.get_mapper('folder/test.zarr'), False, 'zarr'),
        ('https://example.com/test.jpeg', True, 'jpeg'),
        (Path('https://example.com/test.jpeg'), True, 'jpeg'),
        (fsspec.get_mapper('https://example.com/test.jpeg'), True, 'jpeg'),
    ],
)
def test_sanitize_file_path(file_path, should_fail, file_type):
    try:
        sanitized = sanitize_file_path(file_path)
        if not should_fail:
            if file_type == 'nc':
                assert isinstance(sanitized, Path) is True
            elif file_type == 'zarr':
                assert isinstance(sanitized, fsspec.FSMap) is True
    except Exception as e:
        assert isinstance(e, ValueError) is True


@pytest.mark.parametrize(
    "save_path, engine",
    [
        # Netcdf tests
        ('folder/new_test.nc', 'netcdf4'),
        ('folder/new_test.nc', 'zarr'),
        ('folder/path/new_test.nc', 'netcdf4'),
        ('folder/', 'netcdf4'),
        ('s3://ooi-raw-data/', 'netcdf4'),
        (Path('folder/'), 'netcdf4'),
        (Path('folder/new_test.nc'), 'netcdf4'),
        # Zarr tests
        ('folder/new_test.zarr', 'zarr'),
        ('folder/new_test.zarr', 'netcdf4'),
        ('folder/path/new_test.zarr', 'zarr'),
        ('folder/', 'zarr'),
        # Empty tests
        (None, 'netcdf4'),
        (None, 'zarr'),
        # Remotes
        ('https://example.com/test.zarr', 'zarr'),
        ('https://example.com/', 'zarr'),
        ('https://example.com/test.nc', 'netcdf4'),
        ('s3://ooi-raw-data/new_test.zarr', 'zarr'),
        ('s3://ooi-raw-data/new_test.nc', 'netcdf4'),
    ],
)
def test_validate_output_path(save_path, engine, minio_bucket):
    output_root_path = './echopype/test_data/dump'
    source_file = 'test.raw'
    if engine == 'netcdf4':
        ext = '.nc'
    else:
        ext = '.zarr'

    if save_path is not None:
        if '://' not in str(save_path):
            save_path = os.path.join(output_root_path, save_path)
        is_dir = True if Path(save_path).suffix == '' else False
    else:
        is_dir = True
        save_path = output_root_path

    output_storage_options = {}
    if save_path and save_path.startswith("s3://"):
        output_storage_options = dict(
            client_kwargs=dict(endpoint_url="http://localhost:9000/"),
            key="minioadmin",
            secret="minioadmin",
        )

    try:
        output_path = validate_output_path(
            source_file, engine, output_storage_options, save_path
        )

        assert isinstance(output_path, str) is True
        assert Path(output_path).suffix == ext

        if is_dir:
            assert Path(output_path).name == source_file.replace('.raw', '') + ext
        else:
            output_file = Path(save_path)
            assert Path(output_path).name == output_file.name.replace(output_file.suffix, '') + ext
    except Exception as e:
        if 'https://' in save_path:
            if save_path == 'https://example.com/':
                assert isinstance(e, ValueError) is True
                assert str(e) == 'Input file type not supported!'
            elif save_path == 'https://example.com/test.nc':
                assert isinstance(e, ValueError) is True
                assert str(e) == 'Only local netcdf4 is supported.'
            else:
                assert isinstance(e, PermissionError) is True
        elif save_path == 's3://ooi-raw-data/new_test.nc':
            assert isinstance(e, ValueError) is True
            assert str(e) == 'Only local netcdf4 is supported.'


def mock_windows_return(*args: Tuple[str, ...]):
    """
    A function to mock what ``os.path.join`` should
    return on a Windows machine.

    Parameters
    ----------
    args: tuple of str
        A variable number of strings to join

    Returns
    -------
    str
        The input strings joined using Windows syntax
    """
    return r"\\".join(args)


def mock_unix_return(*args: Tuple[str, ...]):
    """
    A function to mock what ``os.path.join`` should
    return on a Unix based machine.

    Parameters
    ----------
    args: tuple of str
        A variable number of strings to join

    Returns
    -------
    str
        The input strings joined using Unix syntax

    Notes
    -----
    This function is necessary just in case the tests are being
    run on a Windows machine.
    """
    return r"/".join(args)


@pytest.mark.parametrize(
    "save_path, is_windows, is_cloud",
    [
        (r"/folder", False, False),
        (r"C:\\folder", True, False),
        (r"s3://folder", False, True),
        (r"s3://folder", True, True),
    ]
)
def test_join_paths(save_path: str, is_windows: bool, is_cloud: bool, monkeypatch):
    """
    Tests the function ``join_paths`` on varying OS and cloud path scenarios by
    adding a folder and a file to the input ``save_path``.

    Parameters
    ----------
    save_path: str
        The save path that we want to add a folder and a file to.
    is_windows: bool
        If True, signifies that we are "working" on a Windows machine,
        otherwise on a Unix based machine
    is_cloud: bool
        If True, signifies that ``save_path`` corresponds to a cloud path,
        otherwise it does not
    """

    # assign the appropriate mock return for os.path.join
    if is_windows:
        monkeypatch.setattr(os.path, 'join', mock_windows_return)
    else:
        monkeypatch.setattr(os.path, 'join', mock_unix_return)

    # add folder and file to path
    joined_path = join_paths(save_path, "output", "data.zarr")

    if is_cloud or (not is_windows):
        assert joined_path == (save_path + r"/output/data.zarr")
    else:
        assert joined_path == (save_path + r"\\output\\data.zarr")
