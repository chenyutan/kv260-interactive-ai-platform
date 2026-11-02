"""Synthetic GTK AI view checks. No API service, key or camera is used."""
import io,time,unittest
from unittest.mock import patch
from types import SimpleNamespace
import cairo
import gi
gi.require_version('Gtk','3.0')
gi.require_version('PangoCairo','1.0')
from gi.repository import Gtk,Gdk,Pango,PangoCairo
from ai_view import AIView,block,step_labels,BODY_SIZE,LINE_HEIGHT
from ai_assistant.catalog import instruction
from gamepad.navigation import Navigation


def drain():
    until=time.monotonic()+.08
    while time.monotonic()<until:
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        time.sleep(.003)


def job(ident='a'*32,kind='creation',status='ready'):
    return dict(id=ident,kind=kind,status=status,result=dict(title='竹林与远山',summary='远山作衬，近处疏竹。',
        steps=[dict(theme=theme,action=action,instruction=instruction(theme,action))
               for theme,action in (('landscape','A01'),('bamboo','A02'),('plum','A03'),('bamboo','A06'))],
        tips=['挥手保持连贯，先熟悉节奏。']*3))


class AIViewTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.view=AIView(self.calls.append)
        self.window=Gtk.Window();self.window.set_decorated(False);self.window.resize(960,540)
        self.window.add(self.view);self.window.show_all();drain()
    def tearDown(self):self.window.destroy();drain()
    def ready(self):self.view.set_context(capabilities={'configured':True})

    def test_missing_health_summary_still_has_course_steps_without_submission(self):
        for project, phrase in (('baduanjin','步骤 1'),('taichi','双臂从身前')):
            self.view.set_context(kind='health',project=project,health_summary=None)
            self.view.select('ai_local_help')
            self.view._prepare_document()
            self.assertIn(phrase,self.view.full_text)
            self.assertEqual(self.calls,[])

    def test_unconfigured_still_allows_return_refresh_type_and_phone(self):
        self.assertFalse(any(c.startswith('ai_preset:') for c in self.view.choices))
        self.assertTrue(self.view.select('ai_refresh'));self.assertTrue(self.view.select('ai_back'))
        self.assertEqual(self.calls,['ai_refresh','ai_back'])
        self.assertFalse(self.view.select('ai_preset:bamboo'))
        self.view.set_availability(False,'摄像头尚未连接')
        self.assertTrue(self.view.select('ai_back'))

    def test_local_type_preset_and_history_pages_never_submit(self):
        self.ready();self.assertTrue(self.view.select('ai_page:next'))
        self.assertIn('ai_preset:mixed',self.view.choices)
        self.view.select('ai_kind:review');self.assertEqual(self.view.kind,'review')
        self.view.set_jobs([job('a'*32,'review'),job('b'*32,'review')])
        self.view.select('ai_job:next');self.assertEqual(self.view.current_job['id'],'b'*32)
        self.view.select('ai_job:previous');self.assertEqual(self.view.current_job['id'],'a'*32)
        self.assertEqual(self.calls,[])

    def test_confirm_emits_only_current_enabled_id(self):
        self.ready();self.view.select('ai_preset:bamboo')
        self.view.select('ai_kind:review');self.view.select('ai_review:improve')
        self.view.select('ai_kind:creation');self.view.set_jobs([job()])
        self.view.select('ai_apply_selected')
        self.assertFalse(self.view.select('ai_apply:'+'z'*32))
        self.assertEqual(self.calls,['ai_preset:bamboo','ai_review:improve','ai_apply:'+'a'*32+':0'])

    def test_each_of_one_to_four_steps_can_be_chosen_and_applied(self):
        self.ready()
        for count in range(1,5):
            item=job();item['result']['steps']=item['result']['steps'][:count];self.view.set_jobs([item])
            for index in range(count):
                before=len(self.calls)
                before_choices=self.view.choices
                self.assertTrue(self.view.select('ai_step:'+str(index)))
                self.assertEqual(len(self.calls),before)
                self.assertEqual(before_choices,self.view.choices)
                self.assertEqual(self.view.step_index,index)
                expected='ai_apply:'+'a'*32+':'+str(index)
                self.assertIn('ai_apply_selected',self.view.choices);self.assertTrue(self.view.select('ai_apply_selected'))
                self.assertEqual(self.calls[-1],expected)
            self.assertFalse(self.view.select('ai_step:'+str(count)))

    def test_running_jobs_block_duplicate_generation_but_never_return(self):
        self.ready();self.view.set_jobs([job(status='running')])
        self.assertTrue(self.view.busy)
        self.assertFalse(any(c.startswith(('ai_preset:','ai_apply')) for c in self.view.choices))
        self.view.select('ai_back');self.assertEqual(self.calls,['ai_back'])

    def test_identical_poll_keeps_dwell_and_type(self):
        self.ready();self.view.select('ai_kind:review');self.view.set_jobs([job(kind='review')])
        index=self.view.choices.index('ai_review:overview')
        self.view.set_gamepad_focus(index);self.view.progress=.7
        self.assertFalse(self.view.set_jobs([job(kind='review')]))
        self.assertFalse(self.view.set_context(capabilities={'configured':True}))
        self.assertEqual(self.view.kind,'review');self.assertEqual(self.view.hover,index)
        self.assertEqual(self.view.progress,.7)

    def test_older_result_stays_selected_when_new_result_arrives(self):
        self.ready();self.view.set_jobs([job('a'*32),job('b'*32)])
        self.view.select('ai_job:next')
        self.view.set_jobs([job('c'*32),job('a'*32),job('b'*32)])
        self.assertEqual(self.view.current_job['id'],'b'*32);self.assertEqual(self.view.job_index,2)

    def test_plain_model_markup_is_not_interpreted(self):
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,500,80)
        literal='<b>模型原文</b> & <script>alert(1)</script>'
        layout=block(cairo.Context(surface),literal,0,0,490,70,18)
        self.assertEqual(layout.get_text(),literal)

    def test_real_action_codes_have_readable_names_and_full_instruction_fits(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,400,100)
        for theme in ('landscape','bamboo','plum'):
            for action in ('A01','A02','A03','A04','A05','A06'):
                value=instruction(theme,action)
                step=dict(theme=theme,action=action,instruction=value)
                title,subtitle=step_labels(step,0)
                self.assertNotEqual(title,'1. '+action)
                self.assertIn(action,subtitle)
                layout=block(cairo.Context(image),value,0,0,384,87,15)
                self.assertFalse(layout.is_ellipsized(),(theme,action,value))
                self.assertLessEqual(layout.get_pixel_size().height,87)

    def test_notice_is_nonmodal_and_preserves_navigation(self):
        self.ready();before=self.view.choices
        self.view.set_gamepad_focus(0);hover=self.view.hover
        self.assertTrue(self.view.set_notice('网络连接暂时不可用，请稍后重试。'))
        self.assertEqual(self.view.choices,before);self.assertEqual(self.view.hover,hover)
        self.view.select('ai_back');self.assertEqual(self.calls,['ai_back'])

    def test_supplied_qr_png_is_local_and_invalid_bytes_do_not_break_return(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,84,84)
        data=io.BytesIO();image.write_to_png(data)
        self.view.set_context(share_url='http://192.168.10.2:8091/demo',qr=data.getvalue())
        self.assertEqual(self.view.qr.get_width(),84)
        self.view.set_context(qr=b'not a PNG')
        self.assertIsNone(self.view.qr);self.assertTrue(self.view.select('ai_back'))

    def test_capability_poll_and_tab_change_preserve_phone_qr(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,84,84)
        data=io.BytesIO();image.write_to_png(data)
        self.view.set_context(capabilities={'configured':True},share_url='http://lan/v/visitor-a',qr=data.getvalue())
        original=self.view.qr;self.view.set_gamepad_focus(0);self.view.progress=.6
        self.assertFalse(self.view.set_context(capabilities={'configured':True}))
        self.assertIs(self.view.qr,original);self.assertEqual(self.view.share_url,'http://lan/v/visitor-a')
        self.assertEqual(self.view.progress,.6)
        self.view.set_context(kind='review')
        self.assertIs(self.view.qr,original);self.assertEqual(self.view.share_url,'http://lan/v/visitor-a')

    def test_explicit_clear_discards_previous_visitor_qr(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,84,84)
        data=io.BytesIO();image.write_to_png(data)
        self.view.set_context(share_url='http://lan/v/visitor-a',qr=data.getvalue())
        self.view.set_context(share_url=None,qr=None)
        self.assertIsNone(self.view.share_url);self.assertIsNone(self.view.qr)

    def test_changed_url_cannot_keep_previous_visitor_qr(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,84,84)
        data=io.BytesIO();image.write_to_png(data)
        self.view.set_context(share_url='http://lan/v/visitor-a',qr=data.getvalue())
        original=self.view.qr
        self.view.set_context(share_url='http://lan/v/visitor-b')
        self.assertEqual(self.view.share_url,'http://lan/v/visitor-b')
        self.assertIsNot(self.view.qr,original)

    def test_leaving_page_clears_old_input_highlight(self):
        self.ready();self.view.set_gamepad_focus(0);self.view.progress=.9
        self.window.hide();drain()
        self.assertIsNone(self.view.hover);self.assertIsNone(self.view.pointer)
        self.assertEqual(self.view.progress,0.)

    def test_every_visible_action_has_disjoint_48px_target_and_is_pad_reachable(self):
        self.ready()
        for kind in ('creation','review'):
            self.view.set_context(kind=kind,capabilities={'configured':True})
            self.view.set_jobs([job('a'*32,kind),job('b'*32,kind)])
            for next_page in (False,True):
                if next_page and 'ai_page:next' in self.view.choices:self.view.select('ai_page:next')
                choices,rects=self.view.choices,self.view.rectangles
                for i,(a,b,c,d) in enumerate(rects):
                    self.assertGreaterEqual((d-b)*540+1e-6,48)
                    self.assertTrue(0<=a<c<=1 and 0<=b<d<=1)
                    self.assertEqual(self.view.hit_index((a+c)/2,(b+d)/2),i)
                    for e,f,g,h in rects[i+1:]:self.assertFalse(a<g and e<c and b<h and f<d)
                seen={0};pending=[0]
                while pending:
                    start=pending.pop()
                    for direction in ('UP','DOWN','LEFT','RIGHT'):
                        nav=Navigation();nav.page='same';nav.index=start
                        dest=nav.update({'buttons':(direction,)},choices,rects,'same',1.)
                        if dest not in seen:seen.add(dest);pending.append(dest)
                self.assertEqual(seen,set(range(len(choices))))

    def test_long_outputs_and_resize_stay_bounded_and_pointer_does_not_rebuild(self):
        self.ready();item=job();item['result']['summary']='很长的模型输出与建议。'*300
        self.view.set_jobs([item])
        for width,height in ((960,540),(1280,720)):
            self.window.resize(width,height);drain()
            image=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            self.view.draw(self.view,cairo.Context(image));builds=self.view.cache_builds
            self.view.set_gamepad_focus(0);self.view.draw(self.view,cairo.Context(image))
            self.assertEqual(builds,self.view.cache_builds)
            self.assertLessEqual(self.view.backbuffer.get_width(),960)
            self.assertLessEqual(self.view.backbuffer.get_height(),540)

    def test_click_and_hand_share_targets_and_page_change_clears_dwell(self):
        self.ready();index=self.view.choices.index('ai_kind:review');a,b,c,d=self.view.rectangles[index]
        self.view.set_gamepad_focus(index);self.view.progress=.9
        self.view.click(self.view,SimpleNamespace(button=1,x=(a+c)*480,y=(b+d)*270))
        self.assertEqual(self.view.kind,'review');self.assertIsNone(self.view.hover)
        self.assertIsNone(self.view.pointer);self.assertEqual(self.view.progress,0)

    def test_full_creation_and_review_lines_are_lossless_at_readable_size(self):
        self.ready()
        for kind in ('creation','review'):
            item=job(kind=kind)
            item['result']['summary']=('远山层叠与近处竹林呼应，让留白形成清晰的空间关系。'*20)[:220]
            item['result']['tips']=[f'第{i+1}项建议：'+('保持连贯节奏，再逐渐尝试较快的动作。'*12)[:180] for i in range(3)]
            self.view.set_context(kind=kind);self.view.set_jobs([item])
            text=self.view.full_text
            self.assertIn(item['result']['summary'],text)
            for value in (item['result']['steps'] if kind=='creation' else item['result']['tips']):
                self.assertIn(value['instruction'] if isinstance(value,dict) else value,text)
            self.assertGreater(len(self.view.text_lines)*LINE_HEIGHT,self.view.body_rect[3]);self.assertGreaterEqual(BODY_SIZE,18)
            self.assertEqual(''.join(text[line.start:line.end] for line in self.view.text_lines),text)
            cr=cairo.Context(cairo.ImageSurface(cairo.FORMAT_RGB24,650,300))
            desc=Pango.FontDescription('Noto Sans CJK SC');desc.set_absolute_size(BODY_SIZE*Pango.SCALE)
            for line in self.view.text_lines:
                layout=PangoCairo.create_layout(cr);layout.set_font_description(desc)
                layout.set_text(text[line.start:line.end].rstrip('\r\n'),-1)
                self.assertFalse(layout.is_ellipsized())
                self.assertLessEqual(layout.get_pixel_size().width,self.view.body_rect[2]+1)
                self.assertLessEqual(layout.get_pixel_size().height,LINE_HEIGHT)

    def test_scrolling_and_steps_do_not_submit_or_change_targets(self):
        self.ready();self.view.set_jobs([job()]);choices=self.view.choices;rects=self.view.rectangles
        self.assertFalse(any(c.startswith('ai_text:') for c in choices))
        for _ in range(20):
            self.view.select('ai_scroll:down')
            self.assertEqual(self.view.choices,choices);self.assertEqual(self.view.rectangles,rects)
        self.assertEqual(self.view.scroll.target,self.view.scroll.limit)
        for index in range(4):
            self.view.select('ai_step:'+str(index))
            offset=self.view.step_offsets[index]
            line=next(i for i,line in enumerate(self.view.text_lines) if line.start<=offset<line.end)
            self.assertEqual(self.view.scroll.target,min(self.view.scroll.limit,line*LINE_HEIGHT))
            self.assertFalse(self.view.scroll.follow)
        self.assertFalse(self.calls)

    def test_first_history_is_immediate_but_new_ready_has_presentation_animation(self):
        self.ready();self.view.set_jobs([job()]);self.assertFalse(self.view.reveal.active)
        self.view.set_jobs([job('b'*32,status='queued'),job()])
        self.view.set_jobs([job('b'*32),job()])
        self.assertTrue(self.view.reveal.active);self.assertEqual(self.view.reveal.shown,0)
        signature=(self.view.choices,self.view.rectangles,self.view.text_lines)
        started=self.view.reveal.started;document_builds=self.view.document_builds
        self.assertFalse(self.view.set_jobs([job('b'*32),job()]))
        self.assertEqual(self.view.reveal.started,started);self.assertEqual(self.view.document_builds,document_builds)
        self.view.reveal.advance(started+.5);self.assertEqual(self.view.reveal.shown,30)
        self.assertEqual(signature,(self.view.choices,self.view.rectangles,self.view.text_lines))
        self.view.select('ai_reveal');self.assertFalse(self.view.reveal.active)
        self.assertEqual(signature,(self.view.choices,self.view.rectangles,self.view.text_lines))

    def test_session_new_ready_animates_and_old_history_does_not_replay(self):
        self.ready();self.view.set_jobs([job()])
        self.view.set_jobs([job('b'*32),job()]);self.assertTrue(self.view.reveal.active)
        self.view.select('ai_job:next');self.assertFalse(self.view.reveal.active)
        self.view.select('ai_job:previous');self.assertFalse(self.view.reveal.active)
        self.view.set_context(share_url=None,qr=None);self.view.set_jobs([])
        self.view.set_jobs([job('b'*32),job()]);self.assertFalse(self.view.reveal.active)

    def test_hidden_page_and_phone_stop_animation_without_retyping(self):
        self.ready();self.view.set_jobs([job(status='queued')]);self.view.set_jobs([job()])
        self.assertTrue(self.view.reveal.active)
        self.window.hide();drain();self.assertFalse(self.view.reveal.active);self.assertIsNone(self.view.timer)
        self.window.show_all();drain();self.assertFalse(self.view.reveal.active)
        position=self.view.scroll.target;self.view.select('ai_phone')
        self.assertTrue(self.view.phone_open);self.assertFalse(self.view.reveal.active)
        self.assertIn('ai_phone_close',self.view.choices)
        self.view.select('ai_phone_close');self.assertEqual(self.view.scroll.target,position)
        self.assertFalse(self.calls)

    def test_animation_repaints_only_answer_and_keeps_static_cache_and_focus(self):
        self.ready();self.view.set_jobs([job(status='running')]);self.view.set_jobs([job()])
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        self.view.draw(self.view,cairo.Context(image));builds=self.view.cache_builds
        index=self.view.choices.index('ai_reveal');self.view.set_gamepad_focus(index)
        self.view.reveal.started=time.monotonic()-100
        drain()
        self.view.draw(self.view,cairo.Context(image))
        self.assertFalse(self.view.reveal.active);self.assertEqual(self.view.hover,index)
        self.assertEqual(self.view.cache_builds,builds)
        body_builds=self.view.body_builds
        self.view.draw(self.view,cairo.Context(image));self.assertEqual(self.view.body_builds,body_builds)
        self.view.update_selection({})
        for _ in range(6):drain()
        self.assertIsNone(self.view.timer)

    def test_native_text_caches_repeat_without_any_pango_work(self):
        self.ready();self.view.set_jobs([job()])
        for size in ((960,540),(1280,720)):
            image=cairo.ImageSurface(cairo.FORMAT_RGB24,*size);cr=cairo.Context(image)
            self.view.paint(cr,size)
            counts=(self.view.cache_builds,self.view.body_builds,self.view.caption_builds)
            body=self.view._body_surface;caption=self.view._caption_surface
            pixels=bytes(image.get_data())
            with patch('ai_view.PangoCairo.create_layout',side_effect=AssertionError('steady paint must not lay out text')):
                for _ in range(5):self.view.paint(cr,size)
            self.assertEqual(counts,(self.view.cache_builds,self.view.body_builds,self.view.caption_builds))
            self.assertIs(body,self.view._body_surface);self.assertIs(caption,self.view._caption_surface)
            self.assertEqual(pixels,bytes(image.get_data()))
            # 720p caches contain native pixels, not a 540p image repeatedly resampled.
            if size==(1280,720):
                self.assertEqual((body.get_width(),body.get_height()),(854,262))
                self.assertEqual((caption.get_width(),caption.get_height()),(304,34))

    def test_resizing_rebuilds_native_caches_once_without_repagination(self):
        self.ready();self.view.set_jobs([job()])
        lines=self.view.text_lines;document_builds=self.view.document_builds
        for size in ((960,540),(1280,720),(960,540)):
            before=(self.view.body_builds,self.view.caption_builds)
            image=cairo.ImageSurface(cairo.FORMAT_RGB24,*size);cr=cairo.Context(image)
            self.view.paint(cr,size)
            self.assertEqual((self.view.body_builds,self.view.caption_builds),tuple(n+1 for n in before))
            self.view.paint(cr,size)
            self.assertEqual((self.view.body_builds,self.view.caption_builds),tuple(n+1 for n in before))
        self.assertIs(lines,self.view.text_lines)
        self.assertEqual(document_builds,self.view.document_builds)

    def test_reveal_rebuilds_only_changed_visible_text_and_completion_caption(self):
        self.ready();self.view.set_jobs([job(status='running')]);self.view.set_jobs([job()])
        size=(1280,720);image=cairo.ImageSurface(cairo.FORMAT_RGB24,*size);cr=cairo.Context(image)
        self.view.paint(cr,size)
        before=(self.view.cache_builds,self.view.body_builds,self.view.caption_builds)
        self.view.reveal.advance(self.view.reveal.started+.1);self.view.paint(cr,size)
        self.assertEqual((self.view.cache_builds,self.view.body_builds,self.view.caption_builds),
                         (before[0],before[1]+1,before[2]))
        self.view.reveal.shown=self.view.text_lines[6].end;self.view.paint(cr,size)
        before=(self.view.cache_builds,self.view.body_builds,self.view.caption_builds)
        self.assertGreater(self.view.reveal.total,self.view.reveal.shown+1)
        self.view.reveal.shown+=1
        with patch('ai_view.PangoCairo.create_layout',side_effect=AssertionError('offscreen reveal must reuse pixels')):
            self.view.paint(cr,size)
        self.assertEqual(before,(self.view.cache_builds,self.view.body_builds,self.view.caption_builds))
        self.view.reveal.finish();self.view.paint(cr,size)
        self.assertEqual((self.view.cache_builds,self.view.body_builds,self.view.caption_builds),
                         (before[0],before[1],before[2]+1))
        self.view.scroll_by(84);self.view.scroll.tick(.1);self.view.paint(cr,size)
        self.assertEqual(self.view.body_builds,before[1]+1)
        self.assertEqual(self.view.caption_builds,before[2]+1)

    def test_phone_targets_are_large_disjoint_and_no_model_html_is_markup(self):
        self.ready();item=job();item['result']['summary']='<b>标题</b> & <script>原样文本</script>'
        self.view.set_jobs([item]);self.assertIn(item['result']['summary'],self.view.full_text)
        self.view.select('ai_phone')
        for i,(a,b,c,d) in enumerate(self.view.rectangles):
            self.assertGreaterEqual((d-b)*540,48)
            for e,f,g,h in self.view.rectangles[i+1:]:self.assertFalse(a<g and e<c and b<h and f<d)

    def test_history_opens_at_top_and_manual_scroll_is_not_stolen_by_poll_or_typing(self):
        self.ready();self.view.set_jobs([job()])
        self.assertEqual(self.view.scroll.offset,0);self.assertFalse(self.view.scroll.follow)
        item=job('b'*32);item['result']['summary']='近景竹林与远山，留白并组织构图。'*12
        self.view.set_jobs([job('b'*32,status='running')]);self.view.set_jobs([item])
        self.view.animate(self.view.reveal.started+4)
        self.assertTrue(self.view.scroll.follow);self.assertGreater(self.view.scroll.target,0)
        self.view.scroll_by(-100000);self.assertFalse(self.view.scroll.follow)
        self.assertEqual(self.view.scroll.target,0)
        self.assertFalse(self.view.set_jobs([item]))
        self.view.animate(self.view.reveal.started+5)
        self.assertEqual(self.view.scroll.target,0)
        self.view.select('ai_reveal');self.assertEqual(self.view.scroll.target,0)
        self.view.scroll_follow();self.assertTrue(self.view.scroll.follow)
        self.assertEqual(self.view.scroll.target,self.view.scroll.limit)

    def test_scroll_limit_stops_at_revealed_content_and_full_text_preserves_mode(self):
        self.ready();self.view.set_jobs([job(status='running')]);self.view.set_jobs([job()])
        self.view.reveal.advance(self.view.reveal.started+.1)
        self.view.scroll_by(100000);self.assertEqual(self.view.scroll.limit,0)
        self.assertFalse(self.view.scroll.follow)
        self.view.select('ai_reveal');self.assertGreater(self.view.scroll.limit,0)
        self.assertEqual(self.view.scroll.target,0)
        self.view.scroll_follow();self.view.select('ai_reveal')
        self.assertEqual(self.view.scroll.target,self.view.scroll.limit)

    def test_controller_scroll_handles_hold_quick_edges_and_preserves_focus(self):
        self.ready();self.view.set_jobs([job()]);index=self.view.choices.index('ai_scroll:down')
        self.view.set_gamepad_focus(index);signature=(self.view.choices,self.view.rectangles,self.view.hover)
        self.assertTrue(self.view.controller_scroll({'buttons':[],'pressed':['RB']},.016))
        self.assertEqual(self.view.scroll.target,28)
        for _ in range(4):self.assertTrue(self.view.controller_scroll({'buttons':['RB']},.05))
        self.assertEqual(self.view.scroll.target,70)
        self.assertEqual(signature,(self.view.choices,self.view.rectangles,self.view.hover))
        self.view.controller_scroll({'buttons':['RB','LB']},.1);self.assertEqual(self.view.scroll.target,70)
        self.view.controller_scroll({'buttons':['RB']},100);self.assertEqual(self.view.scroll.target,80.5)
        self.view.controller_scroll({'buttons':['RB']},float('nan'));self.assertEqual(self.view.scroll.target,80.5)
        self.assertFalse(self.view.controller_scroll({'buttons':['A'],'rx':1},.016))
        self.view.select('ai_phone');self.assertFalse(self.view.controller_scroll({'buttons':['RB']},.016))

    def test_wheel_and_drag_scroll_only_body_and_never_submit(self):
        self.ready();self.view.set_jobs([job()])
        event=SimpleNamespace(x=400,y=250,direction=Gdk.ScrollDirection.DOWN)
        self.assertTrue(self.view.mouse_scroll(self.view,event));self.assertEqual(self.view.scroll.target,84)
        self.assertTrue(self.view.click(self.view,SimpleNamespace(button=1,x=400,y=280)))
        self.view.drag_motion(self.view,SimpleNamespace(x=400,y=240))
        self.assertEqual(self.view.scroll.target,124)
        self.view.drag_release(self.view,event);self.assertIsNone(self.view._drag_y)
        event.x=10;self.assertFalse(self.view.mouse_scroll(self.view,event));self.assertFalse(self.calls)

    def test_long_document_cache_is_viewport_sized_and_glow_quickly_becomes_idle(self):
        self.ready();item=job();item['result']['summary']='山水🌸与竹林，完整正文不应丢失。'*300
        self.view.set_jobs([item]);self.view.scroll_by(100000)
        self.view.scroll.seek(self.view.scroll.target,manual=True,snap=True)
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);cr=cairo.Context(image)
        with patch('ai_view.PangoCairo.create_layout',wraps=PangoCairo.create_layout) as layout:
            self.view._body_image()
        self.assertLessEqual(layout.call_count,8)
        self.assertEqual((self.view._body_surface.get_width(),self.view._body_surface.get_height()),(640,196))
        self.view.set_jobs([job('b'*32,status='queued')]);self.view.set_jobs([job('b'*32)])
        end=self.view.reveal.started+self.view.reveal.total/self.view.reveal.cps+1
        self.view.animate(end);self.assertGreater(self.view._fx_level,0)
        for n in range(30):self.view.animate(end+.04*(n+1))
        self.assertEqual(self.view._fx_level,0);self.assertFalse(self.view.scroll.moving)
        self.view.paint(cr,(960,540));builds=self.view.body_builds
        with patch('ai_view.PangoCairo.create_layout',side_effect=AssertionError('finished answer must be static')):
            self.view.paint(cr,(960,540))
        self.assertEqual(builds,self.view.body_builds)

    def test_continuous_scroll_reuses_visible_line_layouts_with_a_bounded_cache(self):
        self.ready();item=job();item['result']['summary']='近处竹林与远山相映，保留留白并组织清晰的构图。'*100
        self.view.set_jobs([item]);self.view._body_image()
        self.view.scroll.seek(28,manual=True,snap=True)
        with patch('ai_view.PangoCairo.create_layout',wraps=PangoCairo.create_layout) as layouts:
            self.view._body_image()
        self.assertLessEqual(layouts.call_count,1)
        for offset in range(0,int(self.view.scroll.limit),84):
            self.view.scroll.seek(offset,manual=True,snap=True);self.view._body_image()
        self.assertLessEqual(len(self.view._line_layouts),24)


if __name__=='__main__':unittest.main()
