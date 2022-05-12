import hashlib
import json
import logging
import logging.config
from datetime import datetime
from pathlib import Path

from git.repo import Repo
from pydantic import BaseModel
from typing_extensions import Self

from util import progress, sort_dict


DIR = Path(__file__).parent
META_DIR = Path(DIR / "multimc-meta-upstream")
DEFAULT_META_JSON = Path(DIR / "meta.json")
JAR_NAMES = ["client", "server", "windows_server"]


logging.config.fileConfig("logging.conf")
logger = logging.getLogger("meta")


class MetaJar(BaseModel):
    version_id: str
    version_file_id: str
    version_release_time: datetime
    key: str
    sha1: str
    url: str

    def filename(self, extension: str) -> str:
        return f"{self.version_id}-{self.key}.{extension}"

    def update_kwargs(
        self,
        version_id: str,
        version_file_id: str,
        version_release_time: datetime,
        key: str,
        sha1: str,
        url: str,
    ) -> bool:
        dirty = False

        for self_value, arg_value in [
            (self.version_id, version_id),
            (self.version_file_id, version_file_id),
            (self.version_release_time, version_release_time),
            (self.key, key),
        ]:
            if self_value != arg_value:
                raise Exception(f"MetaJar.update_kwargs mismatch {self_value=} {arg_value=}")

        if self.sha1 != sha1:
            self.sha1 = sha1
            dirty = True

        if self.url != url:
            self.url = url
            dirty = True

        return dirty

    def update(self, meta_jar: "MetaJar") -> bool:
        return self.update_kwargs(
            version_id=meta_jar.version_id,
            version_file_id=meta_jar.version_file_id,
            version_release_time=meta_jar.version_release_time,
            key=meta_jar.key,
            sha1=meta_jar.sha1,
            url=meta_jar.url,
        )


class MetaVersion(BaseModel):
    id: str
    file_id: str
    release_time: datetime
    sha1: str
    jars: dict[str, MetaJar]

    @classmethod
    def empty(cls) -> Self:
        return cls(
            id="",
            file_id="",
            release_time=datetime.min,
            sha1="",
            jars=dict(),
        )

    def update(self, version_json: Path) -> bool:
        version_json_sha1 = hashlib.sha1(version_json.read_bytes()).hexdigest()

        if self.sha1 == version_json_sha1:
            # same json file, nothing to do
            logger.info(f"MetaVersion.update {self.file_id} skipped")
            return False

        version_json_data = json.load(version_json.open())

        self.id = version_json_data["id"]
        self.file_id = version_json.stem
        self.release_time = datetime.fromisoformat(version_json_data["releaseTime"])
        self.sha1 = version_json_sha1

        for download_name, download in sorted(version_json_data["downloads"].items()):
            # skip mappings
            if download_name not in JAR_NAMES:
                continue

            meta_jar = MetaJar(
                version_id=self.id,
                version_file_id=self.file_id,
                version_release_time=self.release_time,
                key=download_name,
                sha1=download["sha1"],
                url=download["url"],
            )

            if meta_jar.key not in self.jars:
                self.jars[meta_jar.key] = meta_jar
            else:
                self.jars[meta_jar.key].update(meta_jar)

        # sort jars
        self.jars = sort_dict(self.jars)

        logger.info(f"MetaVersion.update {self.file_id} updated")
        return True


class Meta(BaseModel):
    commit: str
    minecraft: list[MetaVersion]

    @classmethod
    def empty(cls) -> Self:
        return cls(commit="", minecraft=list())

    @classmethod
    def load(cls, file: Path | str = DEFAULT_META_JSON) -> Self:
        logger.info(f"Meta.load {file}")
        return cls.parse_raw(Path(file).read_text())

    def save(self, file: Path | str = DEFAULT_META_JSON) -> None:
        logger.info(f"Meta.save {file}")
        Path(file).write_text(self.json(indent=2))

    def pull_and_update(self) -> bool:
        dirty = False

        # pull submodule
        logger.info(f"Meta.pull_and_update pulling submodule")
        repo = Repo(META_DIR)
        repo.git.pull("origin", "master")

        # compare commit hash
        latest_commit = repo.head.commit.hexsha
        if self.commit == latest_commit:
            # no changes and nothing to do
            logger.info(f"Meta.pull_and_update already up to date")
        else:
            # save latest commit hash
            self.commit = latest_commit

            # update data from submodule
            self.update()

            # there are changes, even if its only the commit hash
            dirty = True
            logger.info(f"Meta.pull_and_update updated to {latest_commit}")

        return dirty

    def update(self) -> bool:
        dirty = False

        # iterate over mojang version json files
        version_json_files = sorted(Path(META_DIR / "mojang/versions").iterdir())

        i_max = len(version_json_files)
        for i, file in enumerate(version_json_files):
            logger.info(f"Meta.update {progress(i, i_max)} {file.stem}")

            # find existing version
            file_version = None
            for existing_version in self.minecraft:
                if existing_version.file_id == file.stem:
                    file_version = existing_version
                    break

            # init version if not found
            if not file_version:
                file_version = MetaVersion.empty()
                self.minecraft.append(file_version)

            # update version
            version_dirty = file_version.update(file)
            dirty = dirty or version_dirty

            logger.info(f"Meta.update {progress(i + 1, i_max)} {file.stem}")

        if dirty:
            # sort by version release time
            self.minecraft.sort(key=lambda v: v.release_time, reverse=True)

        return dirty


if __name__ == "__main__":
    meta = Meta.load()
    if meta.pull_and_update():
        meta.save()
