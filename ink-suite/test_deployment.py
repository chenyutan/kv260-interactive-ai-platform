"""Release integrity checks use synthetic resources and never contact a board."""
import json
import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock,patch

import deploy
import resource_manifest as resources
import upgrade_on_board as upgrade


class ReleaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def put(self,name,data=b'x'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        return path

    def fixture(self):
        model=self.put(resources.MODEL_PATH,b'synthetic model')
        self.put(resources.RUNTIME_PATH+'/onnxruntime/capi/runtime.py',b'pass\n')
        self.put(resources.ASSET_PATH+'/bamboo.png',b'synthetic image')
        for name in resources.REQUIRED_GAME_RESOURCES:self.put(name,b'synthetic asset')
        digest=resources.sha(model)
        self.addCleanup(patch.stopall)
        patch.object(resources,'MODEL_SHA256',digest).start()
        return resources.write_manifest(self.root)

    def test_unsafe_and_noncanonical_paths_rejected(self):
        for name in ('../x','/root/x','C:/x','a\\b','a/../b','a//b','a/./b',''):
            with self.subTest(name=name),self.assertRaises(RuntimeError):resources.safe_path(self.root,name)

    def test_resource_model_hash_is_pinned(self):
        self.fixture();self.put(resources.MODEL_PATH,b'changed model')
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'):resources.verify_resources(self.root)

    def test_unlisted_runtime_file_rejected(self):
        self.fixture();self.put(resources.RUNTIME_PATH+'/unexpected.py')
        with self.assertRaisesRegex(RuntimeError,'whitelist'):resources.verify_resources(self.root)

    def test_unlisted_background_rejected(self):
        self.fixture();self.put(resources.ASSET_PATH+'/extra.png')
        with self.assertRaisesRegex(RuntimeError,'whitelist'):resources.verify_resources(self.root)

    def test_assets_cannot_smuggle_executable_code(self):
        self.fixture();self.put(resources.ASSET_PATH+'/extra.py')
        with self.assertRaisesRegex(RuntimeError,'whitelist'):resources.verify_resources(self.root)

    def test_sword_assets_are_explicit_and_hash_pinned(self):
        self.fixture()
        self.put(resources.SWORD_ASSET_PATH+'/gameplay-atlas.png',b'changed atlas')
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'):resources.verify_resources(self.root)

    def test_sword_assets_cannot_smuggle_code_or_unreviewed_art(self):
        self.fixture()
        for name in ('script.py','extra.png','subfolder/landscape.png'):
            with self.subTest(name=name):
                self.assertFalse(resources.resource_allowed(resources.SWORD_ASSET_PATH+'/'+name))

    def test_candidate_requires_all_sword_assets(self):
        self.fixture()
        for name in resources.REQUIRED_SWORD_ASSETS:
            path=self.root/name;original=path.read_bytes();path.unlink();resources.write_manifest(self.root)
            with self.assertRaisesRegex(RuntimeError,'Missing required sword asset'):upgrade.candidate_files(self.root)
            path.write_bytes(original);resources.write_manifest(self.root)

    def test_generated_python_cache_is_not_packaged_resource(self):
        manifest=self.fixture();self.put(resources.RUNTIME_PATH+'/__pycache__/runtime.pyc')
        self.assertEqual(manifest,resources.verify_resources(self.root))

    def test_new_game_art_and_audio_are_required_and_hash_pinned(self):
        self.fixture()
        for name in resources.REQUIRED_TAIKO_ASSETS + resources.REQUIRED_TAIKO_AUDIO + resources.REQUIRED_SUITE_AUDIO:
            with self.subTest(name=name):
                path=self.root/name;original=path.read_bytes();path.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError,'hash mismatch'):resources.verify_resources(self.root)
                path.unlink();resources.write_manifest(self.root)
                with self.assertRaisesRegex(RuntimeError,'Missing required game resource'):upgrade.candidate_files(self.root)
                path.write_bytes(original);resources.write_manifest(self.root)
        for name in ('taiko/audio/extra.wav','taiko/audio/execute.py','koi/assets/extra.png',
                     'audio/assets/extra.wav','audio/assets/execute.py','audio/preview/review.wav'):
            self.assertFalse(resources.resource_allowed(name))

    def test_relative_resource_root_is_supported(self):
        manifest=self.fixture();previous=Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(manifest,resources.verify_resources(Path('.')))
        finally:os.chdir(previous)

    def test_package_contains_optional_fixture_and_excludes_unlisted_sources(self):
        self.fixture();self.put('main.py',b'x=1\n')
        self.put('avatar/fixtures/recorded_poses.json',b'{}')
        self.put('sword/test_engine.py',b'import unittest\n')
        self.put(resources.SWORD_ASSET_PATH+'/gameplay-atlas.png',b'synthetic atlas')
        self.put('private-experiment.py',b'raise RuntimeError()\n')
        for name in deploy.ASSET_FILES:self.put(name,b'synthetic image')
        resources.write_manifest(self.root)
        with patch.object(deploy,'SUITE_FILES',('main.py',*deploy.NATIVE_FILES)):
            package,_=deploy.build_package(self.root,self.root)
        with tarfile.open(package) as archive:
            names=set(archive.getnames())
            self.assertIn('avatar/fixtures/recorded_poses.json',names)
            self.assertIn('sword/test_engine.py',names)
            self.assertIn(resources.SWORD_ASSET_PATH+'/gameplay-atlas.png',names)
            self.assertNotIn('private-experiment.py',names)
            self.assertNotIn('environment/libink_filter.so',names)
            self.assertNotIn('sword/libink_alpha.so',names)
            self.assertNotIn('sword/libink_rgb565.so',names)
            self.assertTrue(set(deploy.ASSET_FILES).issubset(names))
            self.assertTrue(all(item.mtime==0 for item in archive.getmembers()))

    def test_x86_library_is_rejected_even_with_matching_hash(self):
        self.fixture()
        header=bytearray(20);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(62).to_bytes(2,'little')
        self.put(resources.RUNTIME_PATH+'/onnxruntime/capi/runtime.so',bytes(header))
        with self.assertRaisesRegex(RuntimeError,'Non-AArch64'):resources.write_manifest(self.root)

    def test_symlink_directory_within_resource_tree_is_rejected(self):
        self.fixture();target=self.root/'actual';target.mkdir()
        link=self.root/resources.RUNTIME_PATH/'linked'
        try:link.symlink_to(target,target_is_directory=True)
        except OSError as exc:self.skipTest('Symlinks unavailable: '+str(exc))
        with self.assertRaisesRegex(RuntimeError,'symlink'):resources.verify_resources(self.root)

    def test_candidate_change_after_seal_is_rejected(self):
        self.fixture();self.put('main.py',b'x=1\n')
        header=bytearray(20);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little')
        self.put('environment/libink_filter.so',bytes(header))
        with patch.object(upgrade,'SUITE_FILES',('main.py','environment/libink_filter.so')):
            upgrade.seal_candidate(self.root,{'synthetic_validation':True})
            self.assertIn(upgrade.CANDIDATE_MANIFEST,upgrade.verify_candidate(self.root))
            self.put('main.py',b'x=2\n')
            with self.assertRaisesRegex(RuntimeError,'changed after validation'):upgrade.verify_candidate(self.root)

    def test_sword_accelerator_rejects_host_binary_and_is_hash_pinned(self):
        self.fixture();self.put('main.py',b'x=1\n')
        header=bytearray(20);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little')
        self.put('environment/libink_filter.so',bytes(header))
        with patch.object(upgrade,'SUITE_FILES',('main.py','environment/libink_filter.so','sword/libink_alpha.so')):
            header[18:20]=(62).to_bytes(2,'little')
            self.put('sword/libink_alpha.so',bytes(header))
            with self.assertRaisesRegex(RuntimeError,'libink_alpha.so.*AArch64'):
                upgrade.candidate_files(self.root)
            header[18:20]=(183).to_bytes(2,'little')
            native=self.put('sword/libink_alpha.so',bytes(header))
            candidate=upgrade.seal_candidate(self.root,dict(synthetic_test=True))
            self.assertEqual(candidate['files']['sword/libink_alpha.so'],upgrade.sha(native))
            native.write_bytes(bytes(header)+b'changed')
            with self.assertRaisesRegex(RuntimeError,'changed after validation'):
                upgrade.verify_candidate(self.root)

    def test_missing_current_and_preserved_files_fail_closed(self):
        self.fixture()
        with self.assertRaisesRegex(RuntimeError,'Missing staged source'):upgrade.candidate_files(self.root)
        self.assertTrue({'environment/photo_job.py','environment/onnx_photo_worker.py',
            'environment/ink_finish.py','avatar/main.py','avatar/rig.py','avatar/renderer.py',
            'sword/__init__.py','sword/main.py','sword/engine.py','sword/input.py','sword/renderer.py'}.issubset(upgrade.SUITE_FILES))

    def test_production_is_not_accepted_as_staging_path(self):
        for name in ('/home/ubuntu/ink-suite','/home/ubuntu/bodyink-live','/home/ubuntu/ink-suite-release-staging-a/../ink-suite'):
            with self.subTest(name=name),self.assertRaises(ValueError):deploy.remote_path(name)
        self.assertEqual(deploy.remote_path('/home/ubuntu/ink-suite-release-staging-check'),'/home/ubuntu/ink-suite-release-staging-check')

    def test_expected_absence_is_checked(self):
        upgrade.check_hashes(self.root,{'new.py':None})
        self.put('new.py')
        with self.assertRaisesRegex(RuntimeError,'absent'):upgrade.check_hashes(self.root,{'new.py':None})

    def test_resource_preparation_will_not_overlay_existing_tree(self):
        with self.assertRaisesRegex(RuntimeError,'must be new'):resources.prepare_resources(self.root,self.root)

    def test_retired_experience_is_outside_active_release(self):
        from suite_control import MODES
        from action_dispatch import SUPPORTED_ACTIONS
        from action_catalog import TITLES
        self.assertEqual(MODES,('ink','sword','fight','fruit','taiko'))
        self.assertNotIn('koi',SUPPORTED_ACTIONS);self.assertNotIn('koi',TITLES)
        for names in (upgrade.SUITE_FILES,upgrade.OPTIONAL_TEST_FILES,deploy.ASSET_FILES):
            self.assertFalse(any(name=='koi' or name.startswith('koi/') for name in names))
        self.assertEqual(upgrade.REMOVE_PATHS,('koi',))
        self.assertFalse(resources.resource_allowed('koi/assets/koi.png'))

    def test_candidate_cannot_silently_retain_retired_source(self):
        self.fixture();self.put('koi/main.py',b'pass\n')
        with self.assertRaisesRegex(RuntimeError,'Retired experience'):resources.verify_resources(self.root)

    def test_retired_removal_only_changes_candidate_root(self):
        self.put('koi/main.py',b'old koi')
        keep=('visitor-data/session.db','history/koi-report.json','live-tests/koi-evidence.json')
        for name in keep:self.put(name,b'keep')
        before={name:(self.root/name).read_bytes() for name in keep}
        removed=upgrade.remove_retired_paths(self.root)
        self.assertEqual(removed,['koi']);self.assertFalse((self.root/'koi').exists())
        self.assertEqual({name:(self.root/name).read_bytes() for name in keep},before)
        self.assertEqual(upgrade.remove_retired_paths(self.root),[])

    def test_retired_removal_rejects_production_and_ancestors(self):
        home=self.root/'home';dest=home/'ink-suite';body=home/'bodyink-live'
        for folder in (dest,body,dest/'child',body/'child'):folder.mkdir(parents=True,exist_ok=True)
        with patch.object(upgrade,'HOME',home),patch.object(upgrade,'DEST',dest),patch.object(upgrade,'BODY',body):
            for folder in (self.root,home,dest,body,dest/'child',body/'child'):
                with self.subTest(folder=folder),self.assertRaisesRegex(RuntimeError,'separate candidate'):
                    upgrade.remove_retired_paths(folder)
            candidate=home/'.ink-suite-upgrade-check';candidate.mkdir()
            (candidate/'koi').mkdir();self.assertEqual(upgrade.remove_retired_paths(candidate),['koi'])

    def test_retired_removal_rejects_symlink_before_deleting(self):
        external=self.root/'external';external.mkdir();(external/'keep').write_bytes(b'keep')
        candidate=self.root/'candidate';candidate.mkdir()
        try:(candidate/'koi').symlink_to(external,target_is_directory=True)
        except OSError:self.skipTest('Host does not permit directory symlinks')
        with self.assertRaisesRegex(RuntimeError,'symlink'):upgrade.remove_retired_paths(candidate)
        self.assertEqual((external/'keep').read_bytes(),b'keep')

    def test_cloned_stage_migration_rebuilds_manifest_and_keeps_reports(self):
        self.fixture();self.put('koi/main.py',b'pass\n');asset=self.put('koi/assets/koi.png',b'legacy')
        manifest=json.loads((self.root/resources.MANIFEST_NAME).read_text(encoding='utf-8'))
        manifest['files']['koi/assets/koi.png']=resources.sha(asset)
        manifest['provenance']={'original':'preserved'}
        (self.root/resources.MANIFEST_NAME).write_text(json.dumps(manifest),encoding='utf-8')
        self.put('visitor-data/koi-report.json',b'readable history')
        result=deploy.prepare_retired_stage(self.root)
        self.assertEqual(result['removed_paths'],['koi']);self.assertFalse(result['production_changed'])
        rebuilt=resources.verify_resources(self.root)
        self.assertEqual(rebuilt['provenance'],{'original':'preserved'})
        self.assertFalse(any(name.startswith('koi/') for name in rebuilt['files']))
        self.assertEqual((self.root/'visitor-data/koi-report.json').read_bytes(),b'readable history')

    def test_original_games_are_not_deployable_sources(self):
        self.assertFalse(any('fruit-slice' in name or 'ink-fight' in name for name in upgrade.SUITE_FILES))
        self.assertEqual(upgrade.BODY_SOURCES['native_display.py'],'runtime/native_display.py')
        self.assertEqual([name for name in upgrade.SUITE_FILES if 'native_display' in name],['runtime/native_display.py'])
        self.assertFalse(any('/qa-' in name or '/diagnostics/' in name for name in upgrade.SUITE_FILES))

    def test_only_reviewed_native_display_migrates_out_of_protected_pins(self):
        display='/home/ubuntu/bodyink-live/native_display.py'
        other=('/home/ubuntu/bodyink-live/ink_renderer_fast.py','/home/ubuntu/ink-fight/native/main_v2.py',
               '/home/ubuntu/fruit-slice/native/main.py','/home/ubuntu/bodyink-live/subfolder/native_display.py')
        original=dict.fromkeys((display,*other),'a'*64)
        result=deploy.protected_baseline(original)
        self.assertEqual(result,dict.fromkeys(other,'a'*64));self.assertIn(display,original)
        self.assertEqual(upgrade.BODY_SOURCES['native_display.py'],'runtime/native_display.py')

    def baseline_client(self, data, corrupt=False):
        payload=json.dumps(data).encode('utf-8')
        class Client:
            commands=[]
            def ssh(self,command,*args,**kwargs):
                self.commands.append(command)
                receipt=dict(size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
                return dict(returncode=0,stdout=json.dumps(receipt),stderr='')
            def upload(self,source,target,timeout):
                self.program=source.read_text(encoding='utf-8')
            def download(self,remote,target,timeout):
                target.write_bytes(payload+b'changed' if corrupt else payload)
        return Client()

    def test_large_baseline_uses_verified_file_transfer_not_bounded_stdout(self):
        old=dict(body={},suite={},protected={})
        previous=json.dumps(old).encode('utf-8')
        self.put('expected-upgrade-hashes.json',previous)
        large=dict(body={},protected={},suite={('resource/'+str(i)):('a'*64) for i in range(2500)})
        client=self.baseline_client(large)
        result=deploy.capture_baseline(client,self.root)
        self.assertTrue(result['baseline_refreshed'])
        self.assertEqual(json.loads((self.root/'expected-upgrade-hashes.json').read_text()),large)
        self.assertEqual(Path(result['previous_manifest']).read_bytes(),previous)
        self.assertGreater(len(json.dumps(large)),128*1024)
        self.assertTrue(all(len(command)<256 for command in client.commands))
        self.assertIn("Backend differs from last deployment",client.program)
        self.assertIn("Production drift:",client.program)

    def test_corrupt_baseline_download_does_not_replace_previous_manifest(self):
        old=dict(body={},suite={},protected={})
        previous=json.dumps(old).encode('utf-8')
        self.put('expected-upgrade-hashes.json',previous)
        with self.assertRaisesRegex(RuntimeError,'hash/size mismatch'):
            deploy.capture_baseline(self.baseline_client(old,True),self.root)
        self.assertEqual((self.root/'expected-upgrade-hashes.json').read_bytes(),previous)


class UpgradeRollbackTests(unittest.TestCase):
    """Exercise the real filesystem recovery helper with injected service faults."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.body=self.root/'body';self.dest=self.root/'suite'
        self.backup=self.root/'backup';self.candidate=self.root/'candidate'
        for path in (self.body,self.dest,self.backup/'body',self.backup/'live-suite-at-swap'):
            path.mkdir(parents=True)
        self.sources={'app.py':'runtime/app.py','new.py':'new.py'}
        for name,value in (('BODY',self.body),('DEST',self.dest),('BODY_SOURCES',self.sources)):
            p=patch.object(upgrade,name,value);p.start();self.addCleanup(p.stop)
        (self.body/'app.py').write_bytes(b'candidate app');(self.body/'new.py').write_bytes(b'new module')
        (self.backup/'body/app.py').write_bytes(b'original app')
        (self.dest/'main.py').write_bytes(b'candidate suite')
        saved=self.backup/'live-suite-at-swap/main.py';saved.write_bytes(b'original suite')
        self.protected=self.root/'protected.py';self.protected.write_bytes(b'protected game')
        self.expected=dict(body={'app.py':upgrade.sha(self.backup/'body/app.py'),'new.py':None},
            suite={'main.py':upgrade.sha(saved)},protected={str(self.protected):upgrade.sha(self.protected)})
        self.scene=dict(schema=1,width=1024,height=600,seed=23,events=[dict(action='mountain')])
        self.initial=dict(self.scene,events=[])
        self.body.joinpath('works').mkdir();upgrade.dump(self.backup/'art-scene.json',self.scene)
        upgrade.dump(self.body/'works/autosave-scene.json',dict(self.scene,events=[]))
        upgrade.dump(self.body/'resume-art-once.json',dict(self.scene,events=[]))
        self.order=[]
        self.stop_gui=Mock(side_effect=lambda:self.order.append('stop_gui'))
        self.stop_backend=Mock(side_effect=lambda:self.order.append('stop_backend'))
        def start():
            self.assert_originals();self.order.append('start_backend')
        self.start_backend=Mock(side_effect=start)
        self.health=Mock(side_effect=lambda scene:self.order.append('health'))
        self.menu=Mock(side_effect=lambda:self.order.append('menu'))
        real_copy=upgrade.atomic_copy
        def copy(source,target):
            self.assertIn('stop_gui',self.order);self.assertIn('stop_backend',self.order)
            self.order.append('copy:'+str(target.relative_to(self.root)));real_copy(source,target)
        self.copy=Mock(side_effect=copy)
        p=patch.object(upgrade,'atomic_copy',self.copy);p.start();self.addCleanup(p.stop)

    def recover(self,**changes):
        args=dict(gui_stop_attempted=True,backend_attempted=True,body_touched=True,swapped=True,
            stop_gui=self.stop_gui,stop_backend=self.stop_backend,start_backend=self.start_backend,
            restore_menu=self.menu,verify_backend=self.health)
        checkpoint=changes.pop('checkpoint',self.scene);args.update(changes)
        return upgrade.rollback_suite(self.backup,self.candidate,self.expected,checkpoint,self.initial,**args)

    def assert_originals(self):
        upgrade.check_hashes(self.body,self.expected['body']);upgrade.check_hashes(self.dest,self.expected['suite'])
        upgrade.check_protected(self.expected['protected'])
        for name in ('works/autosave-scene.json','resume-art-once.json'):
            self.assertTrue(upgrade.same_artwork(json.loads((self.body/name).read_text()),self.scene))

    def assert_no_restart(self):
        self.start_backend.assert_not_called();self.health.assert_not_called();self.menu.assert_not_called()

    def test_confirm_both_stops_before_restoring_any_byte(self):
        for failing in ('stop_gui','stop_backend'):
            with self.subTest(failing=failing):
                callback=getattr(self,failing);callback.side_effect=RuntimeError('still running')
                result=self.recover();self.assertTrue(result['rollback_pending'])
                self.assertFalse(result['sources_restored']);self.copy.assert_not_called();self.assert_no_restart()
                self.assertEqual((self.body/'app.py').read_bytes(),b'candidate app')
                callback.side_effect=lambda:self.order.append('stopped')

    def test_success_restores_bytes_absence_and_both_artwork_files_before_start(self):
        result=self.recover();self.assertFalse(result['rollback_pending']);self.assertTrue(result['services_restored'])
        self.assert_originals();self.assertEqual(self.order[:2],['stop_gui','stop_backend'])
        self.assertEqual(self.order[-3:],['start_backend','health','menu'])
        self.assertEqual((self.backup/'failed-candidate-suite/main.py').read_bytes(),b'candidate suite')

    def test_rollback_restores_retired_tree_and_visitor_archive(self):
        saved=self.backup/'live-suite-at-swap'
        koi=saved/'koi/main.py';koi.parent.mkdir();koi.write_bytes(b'original retired game')
        report=saved/'visitor-data/koi-report.json';report.parent.mkdir();report.write_bytes(b'archive')
        self.expected['suite'].update({'koi/main.py':upgrade.sha(koi),'visitor-data/koi-report.json':upgrade.sha(report)})
        result=self.recover();self.assertFalse(result['rollback_pending'])
        self.assertEqual((self.dest/'koi/main.py').read_bytes(),b'original retired game')
        self.assertEqual((self.dest/'visitor-data/koi-report.json').read_bytes(),b'archive')

    def test_copy_error_never_restarts_partial_backend(self):
        self.copy.side_effect=OSError('disk full')
        result=self.recover();self.assertTrue(result['rollback_pending']);self.assert_no_restart()

    def test_bad_restored_hash_never_starts_backend(self):
        (self.backup/'body/app.py').write_bytes(b'corrupt backup')
        result=self.recover();self.assertTrue(result['rollback_pending']);self.assert_no_restart()
        self.assertTrue(any('backend source' in e for e in result['rollback_errors']))

    def test_protected_file_drift_blocks_restart_even_after_source_restore(self):
        self.protected.write_bytes(b'drift')
        result=self.recover();self.assertTrue(result['rollback_pending']);self.assert_no_restart()

    def test_corrupt_artwork_checkpoint_blocks_restart(self):
        upgrade.dump(self.backup/'art-scene.json',dict(self.scene,events=[]))
        result=self.recover();self.assertTrue(result['rollback_pending']);self.assert_no_restart()

    def test_silently_failed_artwork_copy_is_verified_before_start(self):
        copy=self.copy.side_effect
        self.copy.side_effect=lambda s,t:None if t==self.body/'resume-art-once.json' else copy(s,t)
        result=self.recover();self.assertTrue(result['rollback_pending']);self.assert_no_restart()
        self.assertTrue(any('verify saved artwork' in e for e in result['rollback_errors']))

    def test_start_health_or_menu_failure_stops_partially_restarted_services(self):
        for name in ('start_backend','health','menu'):
            with self.subTest(name=name):
                # Successful recovery consumes the suite backup; recreate the
                # transaction filesystem for each injected startup boundary.
                if name!='start_backend':
                    self.dest.rename(self.backup/'live-suite-at-swap')
                callback=getattr(self,name);old=callback.side_effect;callback.side_effect=RuntimeError(name+' failed')
                gui_before=self.stop_gui.call_count;backend_before=self.stop_backend.call_count
                result=self.recover();self.assertTrue(result['sources_restored']);self.assertTrue(result['rollback_pending'])
                self.assertEqual(self.stop_gui.call_count,gui_before+2)
                self.assertEqual(self.stop_backend.call_count,backend_before+2)
                callback.side_effect=old

    def test_partial_directory_swap_is_recovered_even_before_swapped_flag(self):
        self.dest.rename(self.root/'candidate-not-at-destination')
        result=self.recover(swapped=False)
        self.assertFalse(result['rollback_pending']);self.assert_originals()

    def test_modified_files_without_checkpoint_do_not_trust_candidate_autosave(self):
        result=self.recover(checkpoint=None);self.assertTrue(result['rollback_pending']);self.assert_no_restart()

    def test_early_backend_stop_failure_preserves_latest_original_artwork(self):
        self.dest.rename(self.root/'unused-candidate')
        (self.backup/'live-suite-at-swap').rename(self.dest)
        (self.body/'app.py').write_bytes(b'original app');(self.body/'new.py').unlink()
        upgrade.dump(self.body/'works/autosave-scene.json',self.scene)
        result=self.recover(checkpoint=None,body_touched=False,swapped=False)
        self.assertFalse(result['rollback_pending']);self.assert_originals()
        self.assertTrue((self.backup/'rollback-final-art-scene.json').is_file())

    def test_backend_stop_command_success_is_insufficient_without_inactive_zero_pid(self):
        for state in ('ActiveState=active\nMainPID=5\n','ActiveState=inactive\nMainPID=5\n',
                      'ActiveState=deactivating\nMainPID=0\n',''):
            with self.subTest(state=state),patch.object(upgrade,'run',return_value=Mock(stdout=state)):
                with self.assertRaisesRegex(RuntimeError,'not confirmed'):upgrade.stop_rollback_backend()
        with patch.object(upgrade,'run',return_value=Mock(stdout='ActiveState=inactive\nMainPID=0\n')):
            upgrade.stop_rollback_backend()

    def test_orphan_embedded_process_prevents_gui_stop_confirmation(self):
        with patch.object(upgrade,'find_script',side_effect=lambda p:[{'pid':12}] if p==self.dest/'embedded_runner.py' else []):
            with self.assertRaisesRegex(RuntimeError,'process remains'):upgrade.stop_rollback_gui()


if __name__=='__main__':unittest.main()
