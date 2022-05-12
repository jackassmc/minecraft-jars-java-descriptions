import logging
import logging.config
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

from pydantic import BaseModel
from typing_extensions import Self

from jars import DEFAULT_JARS_JSON, Jars, JarsJar
from util import StrAlias, sort_dict


DIR = Path(__file__).parent
DEFAULT_INDEX_JSON = Path(DIR / "index.json")
BASE_URL = "https://jackassmc.github.io/minecraft-jars-java-descriptions"


logging.config.fileConfig("logging.conf")
logger = logging.getLogger("index")


class IndexMapping(BaseModel):
    version_id: str
    version_file_id: str
    version_release_time: datetime
    jar_key: str
    jar_sha1_meta: str
    path: Path
    url: str

    @classmethod
    def from_jar(cls, jar: JarsJar) -> Self:
        return cls(
            version_id=jar.version_id,
            version_file_id=jar.version_file_id,
            version_release_time=jar.version_release_time,
            jar_key=jar.jar_key,
            jar_sha1_meta=jar.jar_sha1_meta,
            path=jar.map_path,
            url=f"{BASE_URL}/{jar.map_path}",
        )

    def update(self, jar: JarsJar) -> bool:
        dirty = False

        for self_value, jar_value in [
            (self.version_id, jar.version_id),
            (self.version_file_id, jar.version_file_id),
            (self.version_release_time, jar.version_release_time),
            (self.jar_key, jar.jar_key),
        ]:
            if self_value != jar_value:
                raise Exception(f"IndexMapping.update mismatch {self_value=} {jar_value=}")

        if self.jar_sha1_meta != jar.jar_sha1_meta:
            self.jar_sha1_meta = jar.jar_sha1_meta
            dirty = True

        if self.path != jar.map_path:
            self.path = jar.map_path
            dirty = True

        url = f"{BASE_URL}/{jar.map_path}"
        if self.url != url:
            self.url = url
            dirty = True

        return dirty


class Index(BaseModel):
    timestamp: datetime
    mappings: dict[StrAlias.minecraft_version, dict[StrAlias.jar_key, IndexMapping]]

    @property
    def all(self) -> chain[IndexMapping]:
        return chain(*[version.values() for version in self.mappings.values()])

    @classmethod
    def empty(cls) -> Self:
        return cls(timestamp=datetime.min, mappings=dict())

    @classmethod
    def load(cls, file: Path | str = DEFAULT_INDEX_JSON) -> Self:
        logger.info(f"Index.load {file}")
        return cls.parse_raw(Path(file).read_text())

    def save(self, file: Path | str = DEFAULT_INDEX_JSON) -> None:
        self.timestamp = datetime.now(timezone.utc)
        logger.info(f"Index.save {file} {self.timestamp.isoformat()}")
        Path(file).write_text(self.json(indent=2))

    def get_version_release_time(self, version: str) -> datetime:
        for mapping in self.all:
            if version in [mapping.version_id, mapping.version_file_id]:
                return mapping.version_release_time
        raise Exception(f"Index.get_version_release_time {version=} not found")

    def update(self, jars_json: Path | str = DEFAULT_JARS_JSON) -> bool:
        dirty = False

        for jar in Jars.load(jars_json).all:
            # init version dict
            if jar.version_id not in self.mappings:
                self.mappings[jar.version_id] = dict()

            # init new jar
            if jar.jar_key not in self.mappings[jar.version_id]:
                self.mappings[jar.version_id][jar.jar_key] = IndexMapping.from_jar(jar)
                dirty = True

            # update existing jar
            else:
                jar_dirty = self.mappings[jar.version_id][jar.jar_key].update(jar)

                # sort by jar key
                if jar_dirty:
                    self.mappings[jar.version_id] = sort_dict(self.mappings[jar.version_id])
                    dirty = True

        if dirty:
            # sort by version release time
            self.mappings = sort_dict(
                self.mappings,
                key=lambda i: self.get_version_release_time(i[0]),
                reverse=True,
            )
            logger.info("Index.update updated")
        else:
            logger.info("Index.update unchanged")

        return dirty


if __name__ == "__main__":
    index = Index.load()
    if index.update():
        index.save()
