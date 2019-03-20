# -*- coding: utf-8 -*-
import argparse
import datetime
import json
import os
import pprint
import time

import requests
import uuid
from mutagen.mp4 import MP4, MP4Cover, AtomDataType
from collections import OrderedDict

from pyaria2 import PyAria2


def generate_filename(value):
    return "".join(i for i in value if i not in r'\/:*?"<>|')


def generate_token():
    return uuid.uuid1().__str__().replace('-', '') + "@@NOLOGIN"


aria2 = PyAria2('localhost', 6800)
pp = pprint.PrettyPrinter(indent=4)

token = generate_token()

HEADERS = OrderedDict([
    ("Content-Type", "application/json; charset=UTF-8"),
    ("Host", "best.rec" + "ochoku.jp"),
    ("Origin", "https://best.rec" + "ochoku.jp"),
    ("Referer", "https://best.reco" + "choku.jp/?"),
    ("User-Agent",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.99"
     " Safari/537.36"),
    ("X-Requested-With", "XMLHttpRequest")]
)

COOKIES = {
    "token": "version%3D0.0%26token%3D{}%26token_limit%3D{}%26artis"
             "t_appeal_display%3D%26player_appeal_display%3D".format(
        token, int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp() * 1000)
    ),
    "player": "volume%3D0.2",
    "_ga": "GA1.3.{}%2540%2540NOLOGIN".format(token),
    "_gid": "GA1.3.1186120077.%d" % int(datetime.datetime.now().timestamp()),
    "_gat": "1"}

API_BASE_URL = "https://best.re" + "cochoku.jp/forc_web/"
IMAGE_BASE_URL = "https://deliver.unlimited.rec" + "ochoku.jp/p/"


class Recochoku:
    def __init__(self):
        self.session = requests.session()
        self.session.headers.update(HEADERS)
        self.session.cookies = requests.utils.cookiejar_from_dict(COOKIES)

    def post(self, uri, data):
        res = self.session.post(API_BASE_URL + uri, data=json.dumps({
            "token": token,
            "dat": data
        }))
        return res.json()["dat"]

    def _get_package_info(self, package_id):
        return self.post("package/top", {"package_id": str(package_id)})

    def get_download_link(self, media_id):
        url = self.post("cdn/hls_url", {"media_id": "%s:0" % (media_id,)})["url"]
        return ("/".join(url.replace("quality/128/", "quality/320/").split('/')[:-1])) + "/"

    @staticmethod
    def _get_track_info(info):
        for d_no, disc in enumerate(info['list']):
            for t_no, track in enumerate(disc):
                yield d_no, t_no, track

    def get_package_info(self, package_id):
        info = self._get_package_info(package_id)
        info["cover_url"] = "{}{}".format(IMAGE_BASE_URL, info['thumb'].replace(".", "_640_640."))
        album_title = info['title']
        artist_name = info['artist_name']
        release_str = info['release']
        info["dir_path"] = os.path.join(os.getcwd(), generate_filename(
            "[{}]{} - {}".format(release_str[2:], album_title, artist_name)))
        
        for d_no, t_no, track in self._get_track_info(info):
            track["download_link"] = self.get_download_link(track["track_id"])
            track["file_name"] = generate_filename("{}.{:0>2d} - {}.m4a".format(d_no + 1, t_no + 1, track["title"]) if len(info['list']) > 1
             else "{:0>2d} - {}.m4a".format(t_no + 1, track["title"]))

        return info

    def download_package(self, package_id):
        info = self.get_package_info(package_id)
        print("Album Info:")
        pp.pprint(info)
        download_gids = self._download_package(info)
        error_gids = []
        print("Waiting for download complete", end='', flush=True)
        while download_gids:
            gid = download_gids.pop()
            status = aria2.tellStatus(gid)['status']
            if status not in ["active", "waiting"]:
                if status != "complete":
                    error_gids.append(gid)
                continue
            download_gids.append(gid)
            time.sleep(1)
            print(".", end="", flush=True)
        self.set_track_info(info, error_gids)

    def _download_package(self, info):
        download_gids = []

        info["cover_gid"] = aria2.addUri([info["cover_url"]],
                                         {"out": "cover.{}".format(info["cover_url"].split('.')[-1]),
                                          "dir": info["dir_path"],
                                          "allow-overwrite": True,
                                          "max-connection-per-server": 4,
                                          "min-split-size": "1M"})
        download_gids.append(info["cover_gid"])

        for d_no, t_no, track in self._get_track_info(info):
            track["gid"] = aria2.addUri([track["download_link"]], {"out": track["file_name"],
                                                                   "dir": info["dir_path"],
                                                                   "allow-overwrite": True,
                                                                   "max-connection-per-server": 16,
                                                                   "min-split-size": "1M",
                                                                   "split": 8})
            print("Add download task {} to aria2.".format(track["file_name"]))
            download_gids.append(track["gid"])
        return download_gids

    def set_track_info(self, info, error_gids):
        cover = None
        if info.get("cover_gid", None) not in error_gids:
            ext = info["cover_url"].split('.')[-1]
            with open(os.path.join(os.path.join(info["dir_path"],
                                                "cover.{}".format(ext))), 'rb') as f:
                cover = MP4Cover(f.read(), AtomDataType.PNG if ext.lower() == 'png' else AtomDataType.JPEG)

        for d_no, t_no, track in self._get_track_info(info):
            if track.get("gid", None) in error_gids:
                print("{} download failed.".format("{:0>2d} - {}.m4a".format(t_no + 1, track["title"])))
                continue

            tags = MP4(os.path.join(info["dir_path"], track["file_name"]))
            tags['trkn'] = ((t_no + 1, len(info['list'][d_no])),)
            tags['disk'] = ((d_no + 1, len(info['list'])),)
            tags['\xa9nam'] = track["title"]
            tags['\xa9alb'] = track["package_name"]
            tags['\xa9ART'] = track["artist_name"]
            tags['aART'] = info["artist_name"]
            tags['\xa9day'] = info["release"][:4]
            tags['cprt'] = info.get("copyright", '')
            if track.get("tieup", None):
                tags['\xa9cmt'] = track["tieup"]
            if cover:
                tags['covr'] = (cover,)
            tags.save()


parser = argparse.ArgumentParser(description='Download album.')
parser.add_argument('aid', type=int, help='album id')

args = parser.parse_args()
Recochoku().download_package(args.aid)
