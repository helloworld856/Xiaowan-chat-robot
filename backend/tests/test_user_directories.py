import json
import logging
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from unittest.mock import Mock


from _imports import BACKEND_ROOT as ROOT

from app.accounts.repository import User
from app.account_logging import account_log_context
from app.account_logging import AccountFileHandler, AccountLabel
from migrate_user_data import migrate_accounts
from app.llm.personal_models import PersonalModel
from app.accounts.profile import Profile, ProfileStore
from app.accounts.preferences import Preferences
from app.accounts.directory import UserDirectoryStore
from app.system_config import DefaultsConfig, LoggingConfig


class UserDirectoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.legacy = Mock()
        self.legacy.find.side_effect = {1: Preferences(send_shortcut='ctrl_enter')}.get
        self.store = UserDirectoryStore(ProfileStore(self.root / 'users'), self.legacy)
        self.admin = User(1, 'admin', 'admin')
        self.member = User(2, 'Member', 'user')
        self.accounts = Mock(list_users=lambda: [self.admin, self.member])

    def test_registration_initializes_private_defaults_without_reading_mysql_preferences(self):
        self.store.prepare_new(self.member)
        root = self.store.paths.directory(self.member.id)
        self.assertEqual(set(path.name for path in root.iterdir()), {'profile.json', 'preferences.json', 'model.json', 'logs'})
        self.assertEqual(self.store.profiles.get(self.member.id, self.member.username).nickname, self.member.username)
        self.assertEqual(self.store.preferences.get(self.member.id).send_shortcut, 'enter')
        self.assertIsNone(self.store.models.get(self.member.id))
        self.legacy.find.assert_not_called()
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / 'model.json').stat().st_mode), 0o600)

    def test_preview_apply_and_rollback_are_repeatable_and_preserve_files(self):
        self.store.profiles.update(self.member.id, Profile(nickname='旧昵称', avatar_id='frog'))
        self.store.models.update(self.member.id, PersonalModel(model_merchant='deepseek', model_name='model',
                                                              api_key='local-test-placeholder'), ())
        personal = self.store.models.path_for(self.member.id)
        original = personal.read_bytes()
        preview = migrate_accounts(self.accounts, self.store)
        self.assertEqual((preview.total, preview.pending, preview.updated), (2, 2, 0))
        self.assertFalse(self.store.paths.directory(self.admin.id).exists())
        self.assertFalse(self.store.preferences.path_for(self.member.id).exists())

        applied = migrate_accounts(self.accounts, self.store, apply=True)
        self.assertEqual((applied.updated, applied.errors), (2, []))
        self.assertEqual(self.store.preferences.get(self.admin.id).send_shortcut, 'ctrl_enter')
        self.assertFalse(self.store.models.path_for(self.admin.id).exists())
        self.assertEqual(personal.read_bytes(), original)
        self.assertEqual(self.store.profiles.get(self.member.id, self.member.username).nickname, '旧昵称')

        self.store.preferences.update(self.admin.id, Preferences())
        self.assertEqual(migrate_accounts(self.accounts, self.store, apply=True).pending, 0)
        self.assertEqual(self.store.preferences.get(self.admin.id).send_shortcut, 'enter')
        rolled_back = migrate_accounts(self.accounts, self.store, rollback=True)
        self.assertEqual(rolled_back.updated, 2)
        self.legacy.update.assert_any_call(self.admin.id, Preferences())

    def test_configured_defaults_apply_to_new_accounts_and_preserve_existing_files(self):
        self.store.prepare_new(self.member)
        defaults = DefaultsConfig(theme='theme3', send_shortcut='ctrl_enter', avatar_id='frog')
        profiles = ProfileStore(self.store.paths, defaults=defaults)
        updated = UserDirectoryStore(profiles, self.legacy, defaults=defaults)
        new_user = User(3, 'NewMember', 'user')
        updated.prepare_new(new_user)
        self.assertEqual(updated.preferences.get(new_user.id), Preferences(theme='theme3', send_shortcut='ctrl_enter'))
        self.assertEqual(profiles.get(new_user.id, new_user.username).avatar_id, 'frog')
        self.assertEqual(updated.preferences.get(self.member.id), Preferences())
        self.assertEqual(profiles.get(self.member.id, self.member.username).avatar_id, 'cat')

    def test_migration_reports_invalid_files_without_replacing_them(self):
        self.store.prepare_new(self.member)
        path = self.store.preferences.path_for(self.member.id)
        path.write_text(json.dumps({'send_shortcut': 'invalid'}), encoding='utf-8')
        contents = path.read_bytes()
        result = migrate_accounts(self.accounts, self.store, apply=True)
        self.assertEqual(result.errors, [(self.member.id, 'ValidationError')])
        self.assertEqual(path.read_bytes(), contents)

    def test_directory_inspection_is_read_only_and_role_specific(self):
        required = {'profile.json', 'preferences.json', 'logs'}
        self.assertEqual({path.name for path in self.store.inspect_missing(self.admin)}, required)
        self.assertEqual({path.name for path in self.store.inspect_missing(self.member)}, required | {'model.json'})
        self.assertFalse(self.store.paths.root.exists())

    def test_themes_survive_reload_and_remain_account_scoped(self):
        self.store.ensure(self.admin)
        self.store.prepare_new(self.member)
        self.store.preferences.update(self.member.id, Preferences(send_shortcut='ctrl_enter', theme='theme2'))
        reloaded = UserDirectoryStore(ProfileStore(self.root / 'users'), self.legacy)
        self.assertEqual(reloaded.preferences.get(self.member.id).model_dump(),
                         {'send_shortcut': 'ctrl_enter', 'theme': 'theme2'})
        self.assertEqual(reloaded.preferences.get(self.admin.id).theme, 'theme0')
        self.assertEqual(json.loads(self.store.preferences.path_for(self.member.id).read_text(encoding='utf-8')),
                         {'send_shortcut': 'ctrl_enter', 'theme': 'theme2'})
        self.store.preferences.path_for(self.admin.id).write_text('{"send_shortcut":"ctrl_enter"}', encoding='utf-8')
        self.assertEqual(reloaded.preferences.get(self.admin.id).theme, 'theme0')

    def test_legacy_lookup_does_not_block_or_overwrite_a_concurrent_save(self):
        updated = Preferences(send_shortcut='ctrl_enter')
        with ThreadPoolExecutor(max_workers=1) as worker:
            # 用另一个线程在旧库读取期间保存新偏好，验证锁顺序。
            def legacy_lookup(user_id):
                worker.submit(self.store.preferences.update, user_id, updated).result(timeout=2)
                return Preferences()

            self.legacy.find.side_effect = legacy_lookup
            self.assertEqual(self.store.preferences.get(self.member.id), updated)

    def test_user_logs_keep_context_when_a_worker_is_reused_and_rotate_private_files(self):
        self.store.prepare_new(self.member)
        system_path = self.root / 'system.log'
        handler = AccountFileHandler(system_path)
        handler.configure(LoggingConfig(max_bytes=120, max_open_account_logs=1, backup_count=1))
        handler.addFilter(AccountLabel())
        handler.setFormatter(logging.Formatter('%(account_label)s %(message)s'))
        logger = logging.Logger('review-test', level=logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(handler.close)

        def log_failure():
            logger.info('member-only')
            raise RuntimeError('synthetic failure')

        with ThreadPoolExecutor(max_workers=1) as worker:
            with account_log_context(self.store.paths, self.member.id):
                with self.assertRaises(RuntimeError):
                    worker.submit(copy_context().run, log_failure).result()
            worker.submit(logger.info, 'system-only').result()
            with account_log_context(self.store.paths, self.admin.id):
                for part in range(3):
                    worker.submit(copy_context().run, logger.info, 'admin-only-%d %s', part, 'x' * 80).result()
        handler.close()

        member_log = self.store.paths.log_directory(self.member.id) / 'app.log'
        admin_dir = self.store.paths.log_directory(self.admin.id)
        self.assertEqual(system_path.read_text(encoding='utf-8').strip(), 'system-only')
        self.assertIn('member-only', member_log.read_text(encoding='utf-8'))
        self.assertNotIn('admin-only', member_log.read_text(encoding='utf-8'))
        self.assertTrue((admin_dir / 'app.log.1').exists())
        self.assertFalse((admin_dir / 'app.log.2').exists())
        self.assertNotIn('member-only', (admin_dir / 'app.log').read_text(encoding='utf-8'))
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE((admin_dir / 'app.log.1').stat().st_mode), 0o600)

    def test_log_write_failure_reports_only_diagnostics_to_system_file(self):
        bad_path = self.store.paths.ensure_directory(self.member.id) / 'logs'
        bad_path.write_text('not a directory', encoding='utf-8')
        system_path = self.root / 'system.log'
        handler = AccountFileHandler(system_path)
        logger = logging.Logger('review-test', level=logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(handler.close)
        with account_log_context(self.store.paths, self.member.id):
            logger.info('original account event')
        handler.close()
        contents = system_path.read_text(encoding='utf-8')
        self.assertIn('运行日志写入失败', contents)
        self.assertNotIn('original account event', contents)
