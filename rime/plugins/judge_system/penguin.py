#!/usr/bin/python
# -*- coding: utf-8 -*-
"""PenguinJudge Plugin

# PROJECT

penguin_config関数にてrime uploadコマンドを使って
PenguinJudgeにコンテスト/問題を登録するする際に利用する情報を定義します。

penguin_configの呼び出しは省略可能です。
また、penguin_configを呼び出した場合も各引数はすべて省略可能です。
url, id, user_id, passwordの指定が省略された場合は、
upload時にプロンプトより入力を求めます。

penguin_configに指定可能な引数は以下のとおりです。

* url: PenguinJudge APIサーバのURLを指定します
* id: コンテストIDを指定します
* user_id: PenguinJudgeの管理者ユーザIDを指定します
* password: user_idにて指定した管理者ユーザのパスワードを指定します
* title: コンテストのタイトルを指定します
* start: コンテストの開始日時をISO8601形式で指定します
* end: コンテストの終了日時をISO8601形式で指定します
* penalty: ペナルティの秒数を指定します

# PROBLEM

penguin_config関数にてPenguinJudge固有の問題設定を定義します。
penguin_configの呼び出しは省略可能です。
また、penguin_configを呼び出した場合も各引数はすべて省略可能です。

penguin_configに指定可能な引数は以下のとおりです。

* memory_limit: メモリ制約をMiB単位で指定します
* score: スコアを整数で指定します
"""

import datetime
from getpass import getpass
import os
import os.path
import json
import sys
from zipfile import ZipFile

from six import BytesIO
from six.moves import http_cookiejar
from six.moves import urllib

from rime.basic import consts
import rime.basic.targets.problem
import rime.basic.targets.project
import rime.basic.targets.solution
import rime.basic.targets.testset  # NOQA
from rime.core import targets
from rime.core import taskgraph
from rime.core.hooks import post_command
from rime.plugins.plus import commands as plus_commands
from rime.util import files


# opener with cookiejar
cookiejar = http_cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookiejar))
penguin_url = None  # for Project._post_command_hook


class Project(targets.registry.Project):
    def PreLoad(self, ui):
        super(Project, self).PreLoad(ui)

        def _parse_datetime(s):
            if s is None:
                return None
            if hasattr(datetime.datetime, 'fromisoformat'):
                try:
                    return datetime.datetime.fromisoformat(s).isoformat()
                except Exception:
                    ui.errors.Error(
                        self, '"{}" is not compatible with ISO8601'.format(s))
            return s  # python3.7未満はfromisoformatがないのでノーチェック

        def _penguin_config(url=None, id=None, user_id=None, password=None,
                            title=None, start=None, end=None, penalty=None):
            global penguin_url
            penguin_url = url
            self.penguin_id = id
            self.penguin_user_id = user_id
            self.penguin_password = password
            self.penguin_title = title
            self.penguin_start = _parse_datetime(start)
            self.penguin_end = _parse_datetime(end)
            if not isinstance(penalty, (int, float, type(None))):
                ui.errors.Error(self, 'penalty must be float')
            self.penguin_penalty = penalty
        if not hasattr(self, 'penguin_id'):
            _penguin_config()
        self.exports['penguin_config'] = _penguin_config
        post_command.Register(self._post_command_hook)

    def _has_token(self):
        for cookie in cookiejar:
            if cookie.name == 'AuthToken':
                return True
        return False

    def _post_command_hook(self, *args, **kwargs):
        if self._has_token():
            self._request('/auth', method='DELETE')

    def _request(self, path, data=None, method=None):
        headers = {}
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode('utf8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(
            penguin_url + path, headers=headers, data=data, method=method)
        return opener.open(req)

    def _login(self, ui):
        def _readline(prompt):
            print(prompt, end=': ')
            sys.stdout.flush()
            return sys.stdin.readline().rstrip()

        global penguin_url
        if not penguin_url:
            penguin_url = _readline('API URL')
        if not self.penguin_id:
            self.penguin_id = _readline('ContestID')
        if not self._has_token():
            if not self.penguin_user_id:
                self.penguin_user_id = _readline('UserID')
            if not self.penguin_password:
                self.penguin_password = getpass()
            self._request('/auth', data={
                'login_id': self.penguin_user_id,
                'password': self.penguin_password
            })

    @taskgraph.task_method
    def Upload(self, ui):
        self._login(ui)

        path = os.path.join(self.base_dir, 'README.md')
        description = None
        if os.path.exists(path):
            with open(path, 'rb') as f:
                description = f.read().decode('utf8')

        try:
            contest = json.loads(self._request(
                '/contests/{}'.format(self.penguin_id)).read().decode('utf8'))
        except Exception:
            if self.penguin_start is None or self.penguin_end is None:
                start = datetime.date.today()
                end = start + datetime.timedelta(days=1)
                self.penguin_start = start.isoformat() + 'T00:00:00+00:00'
                self.penguin_end = end.isoformat() + 'T00:00:00+00:00'
            data = {
                'id': self.penguin_id,
                'title': self.penguin_title if self.penguin_title else 'TITLE',
                "start_time": self.penguin_start,
                "end_time": self.penguin_end,
                'published': False,
                'description': (
                    description if description is not None else 'DESCRIPTION'),
            }
            if self.penguin_penalty:
                data['penalty'] = self.penguin_penalty
            contest = json.loads(self._request(
                '/contests', data=data).read().decode('utf8'))

        patch = {}
        compares = (
            (self.penguin_title, 'title'),
            (self.penguin_start, 'start_time'),
            (self.penguin_end, 'end_time'),
            (self.penguin_penalty, 'penalty'),
            (description, 'description'),
        )
        for v, k in compares:
            if v and v != contest[k]:
                patch[k] = v
        if patch:
            patch = self._request(
                '/contests/{}'.format(self.penguin_id),
                data=patch, method='PATCH').read().decode('utf8')
        yield super(Project, self).Upload(ui)


class Problem(targets.registry.Problem):
    def PreLoad(self, ui):
        super(Problem, self).PreLoad(ui)

        def _penguin_config(memory_limit=None, score=None):
            self.penguin_memory_limit = memory_limit
            self.penguin_score = score
        if not hasattr(self, 'penguin_memory_limit'):
            _penguin_config()
        self.exports['penguin_config'] = _penguin_config


class Testset(targets.registry.Testset):
    def __init__(self, *args, **kwargs):
        super(Testset, self).__init__(*args, **kwargs)
        self.penguin_pack_dir = os.path.join(self.problem.out_dir, 'penguin')


class PenguinPacker(plus_commands.PackerBase):
    @taskgraph.task_method
    def Pack(self, ui, testset):
        testcases = testset.ListTestCases()
        try:
            files.RemoveTree(testset.penguin_pack_dir)
            files.MakeDir(testset.penguin_pack_dir)
            files.MakeDir(os.path.join(testset.penguin_pack_dir, 'input'))
            files.MakeDir(os.path.join(testset.penguin_pack_dir, 'output'))
            files.CopyFile(os.path.join(testset.problem.base_dir, 'README.md'),
                           os.path.join(testset.penguin_pack_dir, 'README.md'))
        except Exception:
            ui.errors.Exception(testset)
            yield False

        for testcase in testcases:
            basename = os.path.splitext(testcase.infile)[0]
            difffile = basename + consts.DIFF_EXT
            packed_infile =\
                os.path.join('input', os.path.basename(basename) + '.in')
            packed_difffile =\
                os.path.join('output', os.path.basename(basename) + '.out')
            try:
                ui.console.PrintAction(
                    'PACK',
                    testset,
                    '%s -> %s' % (os.path.basename(testcase.infile),
                                  packed_infile),
                    progress=True)
                files.CopyFile(os.path.join(testset.out_dir, testcase.infile),
                               os.path.join(testset.penguin_pack_dir,
                                            packed_infile))
                ui.console.PrintAction(
                    'PACK',
                    testset,
                    '%s -> %s' % (os.path.basename(difffile), packed_difffile),
                    progress=True)
                files.CopyFile(os.path.join(testset.out_dir, difffile),
                               os.path.join(testset.penguin_pack_dir,
                                            packed_difffile))
            except Exception:
                ui.errors.Exception(testset)
                yield False

        yield True


class PenguinUploader(plus_commands.UploaderBase):
    @taskgraph.task_method
    def Upload(self, ui, problem, dryrun):
        req = problem.project._request
        problem.project._login(ui)
        with open(os.path.join(problem.base_dir, 'README.md'), 'rb') as f:
            description = f.read().decode('utf8')
        try:
            p = json.loads(req('/contests/{}/problems/{}'.format(
                problem.project.penguin_id, problem.id)).read().decode('utf8'))
        except Exception:
            p = json.loads(req(
                '/contests/{}/problems'.format(problem.project.penguin_id),
                data={
                    'id': problem.id,
                    'title': problem.title,
                    'time_limit': int(problem.timeout),
                    'memory_limit': (
                        problem.penguin_memory_limit
                        if problem.penguin_memory_limit else 512),
                    'score': (
                        problem.penguin_score if problem.penguin_score
                        else 100),
                    'description': description,
                }).read().decode('utf8'))

        patch = {}
        compares = (
            (problem.title, 'title'),
            (problem.timeout, 'time_limit'),
            (problem.penguin_memory_limit, 'memory_limit'),
            (problem.penguin_score, 'score'),
            (description, 'description'),
        )
        for v, k in compares:
            if v and v != p[k]:
                patch[k] = v
        if patch:
            req('/contests/{}/problems/{}'.format(
                problem.project.penguin_id, problem.id
            ), data=patch, method='PATCH')

        testset_dir = problem.testset.penguin_pack_dir
        in_dir = os.path.join(testset_dir, 'input')

        buf = BytesIO()
        with ZipFile(buf, 'w') as zip_file:
            for in_name in os.listdir(in_dir):
                in_path = os.path.join(in_dir, in_name)
                out_name = in_name[:-3] + '.out'
                out_path = os.path.join(testset_dir, 'output', out_name)
                with open(in_path, 'rb') as f:
                    zip_file.writestr(in_name, f.read())
                with open(out_path, 'rb') as f:
                    zip_file.writestr(out_name, f.read())

        req('/contests/{}/problems/{}/tests'.format(
            problem.project.penguin_id, problem.id
        ), data=buf.getvalue(), method='PUT')
        yield True


targets.registry.Override('Project', Project)
targets.registry.Override('Problem', Problem)
targets.registry.Override('Testset', Testset)

plus_commands.packer_registry.Add(PenguinPacker)
plus_commands.uploader_registry.Add(PenguinUploader)
