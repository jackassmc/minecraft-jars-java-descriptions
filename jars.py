import hashlib
import logging
import logging.config
import subprocess
from datetime import datetime
from itertools import chain
from pathlib import Path
from zipfile import ZipFile

import requests
from pydantic import BaseModel
from typing_extensions import Self

from meta import Meta, MetaJar
from util import StrAlias, progress, sort_dict


DIR = Path(__file__).parent
MAPPINGS_DIR = Path(DIR / "mappings")
MAPPINGIO_JAR = Path(DIR / "mapping-io-cli-0.3.0-all.jar")
DEFAULT_JARS_JSON = Path(DIR / "jars.json")


logging.config.fileConfig("logging.conf")
logger = logging.getLogger("jars")


class JarsJar(BaseModel):
    version_id: str
    version_file_id: str
    version_release_time: datetime
    jar_key: str
    jar_sha1_meta: str
    jar_sha1_local: str
    jar_filename: str
    map_filename: str

    @property
    def jar_path(self) -> Path:
        return Path(MAPPINGS_DIR / self.jar_filename).relative_to(DIR)

    @property
    def map_path(self) -> Path:
        return Path(MAPPINGS_DIR / self.map_filename).relative_to(DIR)

    def mappingio(self) -> None:
        mappingio_args = ["convert", self.jar_path, self.map_path, "TINY_2"]
        logger.info(f"JarsJar.mappingio {mappingio_args}")
        return_code = subprocess.call(["java", "-jar", MAPPINGIO_JAR, *mappingio_args])
        if return_code:
            raise Exception("mapping-io-cli error")

    @classmethod
    def from_meta_jar(cls, meta_jar: MetaJar):
        self = cls(
            version_id=meta_jar.version_id,
            version_file_id=meta_jar.version_file_id,
            version_release_time=meta_jar.version_release_time,
            jar_key=meta_jar.key,
            jar_sha1_meta="",
            jar_sha1_local="",
            jar_filename=meta_jar.filename("jar"),
            map_filename=meta_jar.filename("tiny"),
        )
        self.update(meta_jar=meta_jar)
        return self

    def update(self, meta_jar: MetaJar) -> bool:
        dirty = False

        log_prefix = f"JarsJar.update {self.jar_path}"

        # update if meta jar sha1 has changed
        if meta_jar.sha1 != self.jar_sha1_meta:
            # download jar if necessary
            jar_bundle_path = self.jar_path.with_suffix(".bundle.jar")
            if not self.jar_path.exists() or (
                meta_jar.sha1 != hashlib.sha1(self.jar_path.read_bytes()).hexdigest()
                and (
                    not jar_bundle_path.exists()
                    or meta_jar.sha1 != hashlib.sha1(jar_bundle_path.read_bytes()).hexdigest()
                )
            ):
                logger.info(f"{log_prefix} download")
                self.jar_path.write_bytes(requests.get(meta_jar.url).content)

            # extract and rename jar if local jar is a bundle
            unbundled_jar_bytes = None
            with ZipFile(self.jar_path) as jar_zip:
                if "META-INF/versions.list" in jar_zip.namelist():
                    logger.info(f"{log_prefix} extract bundle")

                    versions_list = jar_zip.read("META-INF/versions.list").decode("utf-8")

                    if len(versions_list.splitlines()) > 1:
                        raise Exception(
                            f"{self.jar_path} META-INF/versions.list contains more than one version:\n"
                            + f"---\n{versions_list}\n---"
                        )

                    version_path = versions_list.split("\t")[-1]
                    unbundled_jar_bytes = jar_zip.read(f"META-INF/versions/{version_path}")

            if unbundled_jar_bytes:
                self.jar_path.rename(jar_bundle_path.absolute().relative_to(Path.cwd()))
                self.jar_path.write_bytes(unbundled_jar_bytes)

            # create java descriptions
            self.mappingio()

            # update jar sha1s
            self.jar_sha1_meta = meta_jar.sha1
            self.jar_sha1_local = hashlib.sha1(self.jar_path.read_bytes()).hexdigest()

            logger.info(f"{log_prefix} done")

            dirty = True

        return dirty


class Jars(BaseModel):
    versions: dict[StrAlias.minecraft_version, dict[StrAlias.jar_key, JarsJar]]

    @property
    def all(self) -> chain[JarsJar]:
        return chain(*[version.values() for version in self.versions.values()])

    @classmethod
    def empty(cls) -> Self:
        return cls(versions=dict())

    @classmethod
    def load(cls, file: Path | str = DEFAULT_JARS_JSON) -> Self:
        logger.info(f"Jars.load {file}")
        return cls.parse_raw(Path(file).read_text())

    def save(self, file: Path | str = DEFAULT_JARS_JSON) -> None:
        logger.info(f"Jars.save {file}")
        Path(file).write_text(self.json(indent=2))

    def get_version_release_time(self, version: str) -> datetime:
        for jar in self.all:
            if version in [jar.version_id, jar.version_file_id]:
                return jar.version_release_time
        raise Exception(f"Jars.get_version_release_time {version=} not found")

    def update(self) -> bool:
        dirty = False

        # iterate over minecraft versions in meta
        meta = Meta.load()

        i_max = len(meta.minecraft)
        for i, mc_version in enumerate(reversed(meta.minecraft)):
            prefix_start = f"Jars.update {progress(i, i_max)} {mc_version.id}"
            prefix_end = f"Jars.update {progress(i + 1, i_max)} {mc_version.id}"
            logger.info(prefix_start)

            # init version dict
            if mc_version.id not in self.versions:
                self.versions[mc_version.id] = dict()
                dirty = True

            j_max = len(mc_version.jars)
            for j, (jar_name, meta_jar) in enumerate(mc_version.jars.items()):
                jar_prefix_start = f"{prefix_start} {progress(j, j_max)} {jar_name}"
                jar_prefix_end = f"{prefix_start} {progress(j + 1, j_max)} {jar_name}"
                logger.info(jar_prefix_start)

                # init new jar
                if jar_name not in self.versions[mc_version.id]:
                    self.versions[mc_version.id][jar_name] = JarsJar.from_meta_jar(meta_jar)
                    logger.info(f"{jar_prefix_end} initialized")
                    dirty = True
                    continue

                # update existing jar
                version_dirty = self.versions[mc_version.id][jar_name].update(meta_jar=meta_jar)

                if version_dirty:
                    self.versions[mc_version.id] = sort_dict(self.versions[mc_version.id])
                    logger.info(f"{jar_prefix_end} updated")
                    dirty = True
                else:
                    logger.info(f"{jar_prefix_end} skipped")

            logger.info(f"{prefix_end} done")

        if dirty:
            # sort by version release time
            self.versions = sort_dict(
                self.versions,
                key=lambda i: self.get_version_release_time(i[0]),
                reverse=True,
            )

        return dirty


if __name__ == "__main__":
    jars = Jars.load()
    if jars.update():
        jars.save()
