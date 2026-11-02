"""Menu reachability and hit geometry, without GTK or camera dependencies."""
import math
import unittest
from gamepad.navigation import Navigation
import section_catalog as catalog


class CatalogTests(unittest.TestCase):
    def test_exact_three_home_sections(self):
        self.assertEqual(catalog.CONTENT['home'],('section_entertainment','section_health','section_creation'))
        self.assertEqual(tuple(catalog.TITLES[x] for x in catalog.CONTENT['home']),('娱乐','健康','创作'))

    def test_every_existing_experience_has_one_section(self):
        content=catalog.CONTENT
        modes=content['entertainment']+content['creation']
        self.assertEqual(set(modes),{'ink','sword','fight','fruit','taiko','platformer','runner','pal','kart'})
        self.assertEqual(len(modes),len(set(modes)))
        self.assertEqual(content['health'],('baduanjin',))
        self.assertNotIn('taichi',catalog.TITLES)

    def test_tools_are_public_and_never_substituted_for_sections(self):
        for section in catalog.SECTIONS:
            self.assertEqual(catalog.choices(section,True)[-3:],catalog.PUBLIC_CHOICES)
            self.assertEqual(catalog.choices(section,False)[-1:],('pose_view',))
            self.assertTrue(all(catalog.is_navigation(x) for x in catalog.PUBLIC_CHOICES))

    def test_rectangles_are_finite_in_bounds_disjoint_and_match_choices(self):
        for section in catalog.SECTIONS:
            for visitors in (False,True):
                with self.subTest(section=section,visitors=visitors):
                    rects=catalog.rectangles(section,visitors)
                    self.assertEqual(len(rects),len(catalog.choices(section,visitors)))
                    for index,rect in enumerate(rects):
                        a,b,c,d=rect
                        self.assertTrue(all(math.isfinite(x) for x in rect))
                        self.assertTrue(0<=a<c<=1 and 0<=b<d<=1)
                        self.assertEqual(catalog.hit_index((a+c)/2,(b+d)/2,section,visitors),index)
                        for other in rects[index+1:]:
                            e,f,g,h=other
                            self.assertFalse(a<g and e<c and b<h and f<d)

    def test_all_choices_reachable_by_existing_gamepad_navigation(self):
        for section in catalog.SECTIONS:
            for visitors in (False,True):
                choices=catalog.choices(section,visitors);rects=catalog.rectangles(section,visitors)
                reached={0};todo=[0]
                while todo:
                    start=todo.pop()
                    for direction in ('UP','DOWN','LEFT','RIGHT'):
                        nav=Navigation();nav.page='same';nav.index=start
                        dest=nav.update({'buttons':(direction,)},choices,rects,'same',100.)
                        if dest not in reached:reached.add(dest);todo.append(dest)
                self.assertEqual(reached,set(range(len(choices))),(section,visitors))

    def test_back_routes_exist_on_all_submenus(self):
        for section in catalog.SECTIONS:
            self.assertEqual('section_home' in catalog.choices(section),section!='home')
        for choice,section in zip(catalog.SECTION_CHOICES,catalog.SECTIONS[1:]):
            self.assertEqual(catalog.section_for_choice(choice),section)
        self.assertEqual(catalog.section_for_choice('section_home'),'home')
        self.assertIsNone(catalog.section_for_choice('fruit'))

    def test_inert_margins_and_invalid_coordinates(self):
        for x,y in ((-1,.5),(2,.5),(.5,-1),(.5,2),(float('nan'),.5),(.5,float('inf')),('bad',.5),(None,.5),(.01,.01)):
            self.assertIsNone(catalog.hit_index(x,y))

    def test_unknown_section_rejected(self):
        for function in (catalog.choices,catalog.rectangles,catalog.content_rectangles):
            with self.assertRaises(ValueError):function('not-a-section')

    def test_course_demonstration_can_open_without_live_pose(self):
        self.assertTrue(catalog.available_without_pose('baduanjin'))
        self.assertFalse(catalog.available_without_pose('fruit'))
        self.assertFalse(catalog.available_without_pose('ink'))

if __name__=='__main__':unittest.main()
