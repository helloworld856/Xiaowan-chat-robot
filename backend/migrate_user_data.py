"""Preview, apply, or roll back the account preferences migration offline."""

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pymysql
from dotenv import load_dotenv

from app.accounts.repository import SQLAccountStore, User
from app.database.mysql import open_pool
from app.accounts.profile import ProfileStore
from app.accounts.preferences import Preferences, UserPreferencesStore
from app.llm.admin_settings import settings_store
from app.accounts.directory import UserDirectoryStore


@dataclass
class MigrationReport:
    total: int = 0
    pending: int = 0
    updated: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)


def migrate_accounts(accounts: SQLAccountStore, directories: UserDirectoryStore, *, apply=False, rollback=False,
                     legacy_profile: Path | None = None, admin_preferences: Preferences | None = None) -> MigrationReport:
    report = MigrationReport()
    legacy = directories.preferences.legacy
    for user in accounts.list_users():
        report.total += 1
        try:
            if user.role not in ('admin', 'user'):
                raise ValueError('账号角色无效')
            if rollback:
                try:
                    saved = directories.preferences.read_file(directories.preferences.path_for(user.id))
                except FileNotFoundError:
                    continue
                legacy.update(user.id, saved)
                report.updated += 1
                continue

            missing = directories.inspect_missing(user)
            report.pending += int(bool(missing))
            defaults = None
            if directories.preferences.path_for(user.id) in missing:
                defaults = legacy.find(user.id)
                if user.role == 'admin' and defaults is None:
                    defaults = admin_preferences
                defaults = defaults or Preferences()

            import_profile = (user.role == 'admin' and legacy_profile is not None
                              and directories.profiles.path_for(user.id) in missing)
            if import_profile:
                try:
                    directories.profiles.read_file(legacy_profile)
                except FileNotFoundError:
                    import_profile = False
            if not apply:
                continue
            if import_profile:
                directories.profiles.import_legacy_profile(user.id, legacy_profile)
            directories.ensure(user, defaults)
            report.updated += int(bool(missing))
        except (OSError, ValueError, pymysql.MySQLError) as error:
            report.errors.append((user.id, type(error).__name__))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description='检查或迁移现有账号的独立用户目录')
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--dry-run', action='store_true', help='检查已有文件并统计缺失账号')
    actions.add_argument('--apply', action='store_true', help='补齐目录并迁入个人偏好')
    actions.add_argument('--rollback-preferences', action='store_true', help='将当前文件中的个人偏好回填到 MySQL')
    args = parser.parse_args()

    backend = Path(__file__).resolve().parent
    load_dotenv(backend / '.env')
    pool = open_pool(create_if_missing=False)
    try:
        try:
            admin_preferences = settings_store.get().preferences
        except (OSError, ValueError):
            admin_preferences = None
        directories = UserDirectoryStore(ProfileStore(), UserPreferencesStore(pool))
        report = migrate_accounts(SQLAccountStore(pool), directories, apply=args.apply,
                                  rollback=args.rollback_preferences, legacy_profile=backend / 'auth_config' / 'profile.json',
                                  admin_preferences=admin_preferences)
    except (OSError, pymysql.MySQLError) as error:
        print(f'无法读取用户数据库，请检查 MySQL 服务和连接配置（{type(error).__name__}）')
        return 1
    finally:
        pool.close()

    print(f'账号 {report.total} 个，缺少文件 {report.pending} 个，写入 {report.updated} 个，异常 {len(report.errors)} 个。')
    for user_id, error in report.errors:
        print(f'账号 {user_id}：{error}，请检查对应目录或数据库偏好。')
    return int(bool(report.errors))


if __name__ == '__main__':
    raise SystemExit(main())
