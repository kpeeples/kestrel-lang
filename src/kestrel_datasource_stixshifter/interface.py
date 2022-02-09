"""The STIX shifter data source package provides access to data sources via
`stix-shifter`_.

Optional before use: install any target stix-shifter connector packages such as
``stix-shifter-modules-carbonblack``. This STIX shifter interface will try to
guess the target connector and install if not exist, but the connector naming
guess may not always be correct. Manual install is required if a user see error
plus suggestion to install a connector. Check all `avaliable connectors`_ and
their `pypi packages <https://pypi.org/search/?q=stix-shifter-modules&o=>`_.

The STIX Shifter interface can reach multiple data sources. The user needs to
provide one *profile* per data source. The profile name (case insensitive) will
be used in the ``FROM`` clause of the Kestrel ``GET`` command, e.g., ``newvar =
GET entity-type FROM stixshifter://profilename WHERE ...``. Kestrel runtime
will load profiles from 3 places (the later will override the former):

#. stix-shifter interface config file (only when a Kestrel session starts):

    Put your profiles in the stix-shifter interface config file (YAML):

    - Default path: ``~/.config/kestrel/stixshifter.yaml``.
    - A customized path specified in the environment variable ``KESTREL_STIXSHIFTER_CONFIG``.

    Example of stix-shifter interface config file containing profiles:

    .. code-block:: yaml

        profiles:
            host101:
                connector: elastic_ecs
                connection:
                    host: elastic.securitylog.company.com
                    port: 9200
                    # stix-shifter will NOT verify certificate with the following flag
                    selfSignedCert: false
                    indices: host101
                config:
                    auth:
                        id: VuaCfGcBCdbkQm-e5aOx
                        api_key: ui2lp2axTNmsyakw9tvNnw
            host102:
                connector: qradar
                connection:
                    host: qradar.securitylog.company.com
                    port: 443
                config:
                    auth:
                        SEC: 123e4567-e89b-12d3-a456-426614174000
            host103:
                connector: cbcloud
                connection:
                    host: cbcloud.securitylog.company.com
                    port: 443
                config:
                    auth:
                        org-key: D5DQRHQP
                        token: HT8EMI32DSIMAQ7DJM

#. environment variables (only when a Kestrel session starts):

    Three environment variables are required for each profile:

    - ``STIXSHIFTER_PROFILENAME_CONNECTOR``: the STIX Shifter connector name,
      e.g., ``elastic_ecs``.
    - ``STIXSHIFTER_PROFILENAME_CONNECTION``: the STIX Shifter `connection
      <https://github.com/opencybersecurityalliance/stix-shifter/blob/master/OVERVIEW.md#connection>`_
      object in JSON string.
    - ``STIXSHIFTER_PROFILENAME_CONFIG``: the STIX Shifter `configuration
      <https://github.com/opencybersecurityalliance/stix-shifter/blob/master/OVERVIEW.md#configuration>`_
      object in JSON string.

    Example of environment variables for a profile:

    .. code-block:: console

        $ export STIXSHIFTER_HOST101_CONNECTOR=elastic_ecs
        $ export STIXSHIFTER_HOST101_CONNECTION='{"host":"elastic.securitylog.company.com", "port":9200, "indices":"host101"}'
        $ export STIXSHIFTER_HOST101_CONFIG='{"auth":{"id":"VuaCfGcBCdbkQm-e5aOx", "api_key":"ui2lp2axTNmsyakw9tvNnw"}}'

#. any in-session edit through the ``CONFIG`` command.

If you launch Kestrel in debug mode, stix-shifter debug mode is still not
enabled by default. To record debug level logs of stix-shifter, create
environment variable ``KESTREL_STIXSHIFTER_DEBUG`` with any value.

.. _stix-shifter: https://github.com/opencybersecurityalliance/stix-shifter
.. _avaliable connectors: https://github.com/opencybersecurityalliance/stix-shifter/blob/develop/OVERVIEW.md#available-connectors

"""

import sys
import json
import time
import copy
import logging
import importlib
import subprocess

from stix_shifter.stix_translation import stix_translation
from stix_shifter.stix_transmission import stix_transmission

from kestrel.utils import mkdtemp
from kestrel.datasource import AbstractDataSourceInterface
from kestrel.datasource import ReturnFromFile
from kestrel.exceptions import DataSourceError, DataSourceManagerInternalError
from kestrel_datasource_stixshifter.config import (
    RETRIEVAL_BATCH_SIZE,
    get_datasource_from_profiles,
    load_profiles,
    set_stixshifter_logging_level,
)

_logger = logging.getLogger(__name__)


def check_module_availability(connector_name):
    try:
        importlib.import_module(
            "stix_shifter_modules." + connector_name + ".entry_point"
        )
    except:
        package_name = "stix-shifter-modules-" + connector_name.replace("_", "-")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        try:
            importlib.import_module(
                "stix_shifter_modules." + connector_name + ".entry_point"
            )
        except:
            raise DataSourceError(
                f'STIX shifter connector for "{connector_name}" is not installed '
                + "and Kestrel cannot figure out the correct Python package name for install",
                "please manually install the corresponding STIX shifter connector Python package.",
            )


class StixShifterInterface(AbstractDataSourceInterface):
    @staticmethod
    def schemes():
        """STIX Shifter data source interface only supports ``stixshifter://`` scheme."""
        return ["stixshifter"]

    @staticmethod
    def list_data_sources(config):
        """Get configured data sources from environment variable profiles."""
        if not config:
            config["profiles"] = load_profiles()
        data_sources = list(config["profiles"].keys())
        data_sources.sort()
        return data_sources

    @staticmethod
    def query(uri, pattern, session_id, config):
        """Query a stixshifter data source."""
        scheme, _, profile = uri.rpartition("://")
        profiles = profile.split(",")

        if not config:
            config["profiles"] = load_profiles()

        if scheme != "stixshifter":
            raise DataSourceManagerInternalError(
                f"interface {__package__} should not process scheme {scheme}"
            )

        set_stixshifter_logging_level()

        ingestdir = mkdtemp()
        query_id = ingestdir.name
        bundles = []
        _logger.debug(f"prepare query with ID: {query_id}")
        for i, profile in enumerate(profiles):
            # STIX-shifter will alter the config objects, thus making them not reusable.
            # So only give stix-shifter a copy of the configs.
            # Check `modernize` functions in the `stix_shifter_utils` for details.
            (connector_name, connection_dict, configuration_dict,) = map(
                copy.deepcopy, get_datasource_from_profiles(profile, config["profiles"])
            )

            check_module_availability(connector_name)

            data_path_striped = "".join(filter(str.isalnum, profile))
            ingestfile = ingestdir / f"{i}_{data_path_striped}.json"

            query_metadata = json.dumps(
                {"id": "identity--" + query_id, "name": connector_name}
            )

            translation = stix_translation.StixTranslation()
            transmission = stix_transmission.StixTransmission(
                connector_name, connection_dict, configuration_dict
            )

            dsl = translation.translate(
                connector_name, "query", query_metadata, pattern, {}
            )

            if "error" in dsl:
                raise DataSourceError(
                    f"STIX-shifter translation failed with message: {dsl['error']}"
                )

            _logger.debug(f"STIX pattern to query: {pattern}")
            _logger.debug(f"translate results: {dsl}")

            # query results should be put together; when translated to STIX, the relation between them will remain
            connector_results = []
            for query in dsl["queries"]:
                search_meta_result = transmission.query(query)
                if search_meta_result["success"]:
                    search_id = search_meta_result["search_id"]
                    if transmission.is_async():
                        time.sleep(1)
                        status = transmission.status(search_id)
                        if status["success"]:
                            while (
                                status["progress"] < 100
                                and status["status"] == "RUNNING"
                            ):
                                status = transmission.status(search_id)
                        else:
                            stix_shifter_error_msg = (
                                status["error"]
                                if "error" in status
                                else "details not avaliable"
                            )
                            raise DataSourceError(
                                f"STIX-shifter transmission.status() failed with message: {stix_shifter_error_msg}"
                            )

                    result_retrieval_offset = 0
                    has_remaining_results = True
                    while has_remaining_results:
                        result_batch = transmission.results(
                            search_id, result_retrieval_offset, RETRIEVAL_BATCH_SIZE
                        )
                        if result_batch["success"]:
                            new_entries = result_batch["data"]
                            if new_entries:
                                connector_results += new_entries
                                result_retrieval_offset += RETRIEVAL_BATCH_SIZE
                                if len(new_entries) < RETRIEVAL_BATCH_SIZE:
                                    has_remaining_results = False
                            else:
                                has_remaining_results = False
                        else:
                            stix_shifter_error_msg = (
                                result_batch["error"]
                                if "error" in result_batch
                                else "details not avaliable"
                            )
                            raise DataSourceError(
                                f"STIX-shifter transmission.results() failed with message: {stix_shifter_error_msg}"
                            )

                else:
                    stix_shifter_error_msg = (
                        search_meta_result["error"]
                        if "error" in search_meta_result
                        else "details not avaliable"
                    )
                    raise DataSourceError(
                        f"STIX-shifter transmission.query() failed with message: {stix_shifter_error_msg}"
                    )

            _logger.debug("transmission succeeded, start translate back to STIX")

            stixbundle = translation.translate(
                connector_name,
                "results",
                query_metadata,
                json.dumps(connector_results),
                {},
            )

            _logger.debug(f"dumping STIX bundles into file: {ingestfile}")
            with ingestfile.open("w") as ingest:
                json.dump(stixbundle, ingest, indent=4)
            bundles.append(str(ingestfile.expanduser().resolve()))

        return ReturnFromFile(query_id, bundles)
