"""What each scenario of the Automaton tab is about (the same texts as Maprisma Playground's
automaton_info.dart): the named rules, an own rule, drawing and the patterns. Shown in the hint
window behind the ⓘ beside the rule and pattern choices; "Try it" sets up `setup` and starts it.
The texts are English keys for _()."""

MATERIALS_NOTE = 'An illustration with simple rules, not a simulation of a real material: it shows how such a process looks, not its times, temperatures or strengths.'

RULE_INFO = {"Conway's Life": {'what': 'The classic. A dead cell with exactly 3 live neighbours is born; a live cell '
                           'with 2 or 3 live neighbours survives, any other dies of loneliness or '
                           'overcrowding. From random noise come a few hundred generations of chaos that '
                           'settle into still lifes, blinkers and gliders flying away.',
                   'watch': 'Gliders: small young shapes in yellow and orange that move diagonally. What is '
                            'left at the end turns cyan and violet: blocks, beehives and other still lifes, '
                            'and blinkers that keep turning.',
                   'try': 'A random fill of 30 %, or the Gosper glider gun on an empty grid.',
                   'setup': {'rule': 'B3/S23', 'density': 0.3},
                   'links': [("Conway's Game of Life",
                              'https://en.wikipedia.org/wiki/Conway%27s_Game_of_Life'),
                             ('Still life', 'https://en.wikipedia.org/wiki/Still_life_(cellular_automaton)'),
                             ('Glider', 'https://en.wikipedia.org/wiki/Glider_(Conway%27s_Game_of_Life)')]},
 'HighLife': {'what': 'Like Life, but a dead cell is also born with 6 live neighbours. Almost everything '
                      'behaves as in Life, but HighLife has a famous replicator: a small shape that makes '
                      'copies of itself again and again.',
              'watch': 'Patches that look like Life but now and then grow in a new way; compare it with '
                       "Conway's Life on the same kind of fill.",
              'try': 'A random fill of 25 %.',
              'setup': {'rule': 'B36/S23', 'density': 0.25},
              'links': [('Highlife', 'https://en.wikipedia.org/wiki/Highlife_(cellular_automaton)'),
                        ('Replicator', 'https://en.wikipedia.org/wiki/Replicator_(cellular_automaton)')]},
 'Seeds': {'what': 'Every live cell dies at once; a dead cell with exactly 2 live neighbours is born. '
                   'Nothing ever stands still: two cells are enough for an explosion that spreads like '
                   'fireworks over the grid.',
           'watch': 'Everything is green (just born) and red (just died): no cell lives longer than one '
                    'generation.',
           'try': 'An empty grid with only a few cells: here two cells side by side in the middle.',
           'setup': {'rule': 'B2/S', 'pattern': 'Seeds pair'},
           'links': [('Seeds', 'https://en.wikipedia.org/wiki/Seeds_(cellular_automaton)')]},
 'Day & Night': {'what': 'Live and dead cells follow the same rule, mirrored: dead cells in a sea of live '
                         'ones behave exactly like live cells in a sea of dead ones. Random noise turns into '
                         'growing blobs of "day" and "night" with wandering borders.',
                 'watch': 'The violet inside of the blobs (old, standing still) and the green and yellow '
                          'borders where they grow.',
                 'try': 'A random fill of 50 %.',
                 'setup': {'rule': 'B3678/S34678', 'density': 0.5},
                 'links': [('Day and Night',
                            'https://en.wikipedia.org/wiki/Day_and_Night_(cellular_automaton)')]},
 'Maze': {'what': 'Cells survive with 1 to 5 live neighbours, so almost nothing dies. A small seed grows '
                  'into a maze of corridors that fills the grid.',
          'watch': 'The walls of the maze turn violet as they settle; only the growing ends stay young.',
          'try': 'A random fill of 10 %.',
          'setup': {'rule': 'B3/S12345', 'density': 0.1},
          'links': [('Life-like cellular automaton',
                     'https://en.wikipedia.org/wiki/Life-like_cellular_automaton')]},
 'Life without Death': {'what': 'Nothing ever dies: cells are only born, with 3 live neighbours. Patterns '
                                'grow into ladders and frozen trees that never shrink.',
                        'watch': 'The growing front is green and yellow, the old body violet: you see how it '
                                 'grew.',
                        'try': 'The R-pentomino on an empty grid.',
                        'setup': {'rule': 'B3/S012345678', 'pattern': 'R-pentomino'},
                        'links': [('Life without Death',
                                   'https://en.wikipedia.org/wiki/Life_without_Death')]},
 'Replicator': {'what': 'Only odd numbers of neighbours count. Any shape is copied into more and more copies '
                        'of itself, so a small drawing turns into ever larger repeated mosaics.',
                'watch': 'Step through it (→) and see the first shape appear again in four, then sixteen '
                         'places.',
                'try': 'The glider on an empty grid, step by step.',
                'setup': {'rule': 'B1357/S1357', 'pattern': 'Glider', 'play': False},
                'links': [('Replicator', 'https://en.wikipedia.org/wiki/Replicator_(cellular_automaton)')]},
 "Brian's Brain": {'what': 'Three states: a live cell always becomes "dying" in the next generation (it '
                           'fades) and then dead; a dead cell with exactly 2 live neighbours is born. No '
                           'cell survives, so everything moves: endless streams of small spaceships cross '
                           'the grid.',
                   'watch': 'The fading tails behind the moving fronts show which way they fly.',
                   'try': 'A random fill of 20 %.',
                   'setup': {'rule': 'B2/S/C3', 'density': 0.2},
                   'links': [("Brian's Brain", 'https://en.wikipedia.org/wiki/Brian%27s_Brain')]},
 'Star Wars': {'what': 'Four states: cells survive with 3 to 5 live neighbours and fade over two generations '
                       'when they die. Fast spaceships and long-lived structures fight each other.',
               'watch': 'Old structures in cyan and violet that are hit by young spaceships and break apart.',
               'try': 'A random fill of 30 %.',
               'setup': {'rule': 'B2/S345/C4', 'density': 0.3},
               'links': [("Brian's Brain (Generations rules)",
                          'https://en.wikipedia.org/wiki/Brian%27s_Brain'),
                         ('Spaceship', 'https://en.wikipedia.org/wiki/Spaceship_(cellular_automaton)')]},
 'Plastics: demixing of a blend': {'what': 'Two plastics that do not mix well (a polymer blend) are stirred '
                                           'into each other while hot. As the blend cools, each cell takes '
                                           'on what most of its neighbours are: the two plastics separate '
                                           'into islands that grow and become rounder, as in spinodal '
                                           'decomposition. Live cells are one plastic, empty cells the '
                                           'other. The rule is known as Anneal.',
                                   'watch': 'The fine noise of the start turns into islands within a few '
                                            'generations; then they slowly grow and their borders smooth '
                                            'out. The inside of the islands turns violet (it no longer '
                                            'changes), the borders stay young.',
                                   'try': 'A random fill of 50 %: an evenly stirred blend.',
                                   'setup': {'rule': 'B4678/S35678', 'density': 0.5},
                                   'links': [('Spinodal decomposition',
                                              'https://en.wikipedia.org/wiki/Spinodal_decomposition'),
                                             ('Polymer blend', 'https://en.wikipedia.org/wiki/Polymer_blend'),
                                             ('Life-like cellular automaton (Anneal)',
                                              'https://en.wikipedia.org/wiki/Life-like_cellular_automaton')]},
 'Plastics: crystallization': {'what': 'A semi-crystalline plastic (such as polypropylene) cools down: at a '
                                       'few points, nuclei form, and crystals grow from each of them until '
                                       'they meet their neighbours (spherulites). Every empty cell next to a '
                                       'crystal becomes crystal; nothing melts again.',
                               'watch': 'Each nucleus grows into a square grain; where the grains meet, '
                                        'their growth stops. The colours show their age: the centre of each '
                                        'grain is oldest, the meeting lines youngest.',
                               'try': 'Very few nuclei: a random fill of 0.3 %.',
                               'setup': {'rule': 'B1/S012345678', 'density': 0.003},
                               'links': [('Crystallization of polymers',
                                          'https://en.wikipedia.org/wiki/Crystallization_of_polymers'),
                                         ('Spherulite',
                                          'https://en.wikipedia.org/wiki/Spherulite_(polymer_physics)'),
                                         ('Nucleation', 'https://en.wikipedia.org/wiki/Nucleation')]},
 'Adhesive: curing': {'what': 'A reactive adhesive or resin cures: where cross-linking has started, it '
                              'spreads to the neighbours, and what is cured stays cured. A spot cures when 3 '
                              'or more of its neighbours have cured (5 to 8 also fill the gaps), so curing '
                              'fronts grow, close holes and join.',
                      'watch': 'Small cured spots grow into solid areas. Below about 15 % at the start, many '
                               'stay separate islands; above it, everything joins into one network: the gel '
                               'point, where the adhesive starts to hold together. Cured areas turn violet, '
                               'the fronts stay green.',
                      'try': 'A random fill of 20 %; then compare Random at 10 % and at 30 %.',
                      'setup': {'rule': 'B35678/S012345678', 'density': 0.2},
                      'links': [('Curing', 'https://en.wikipedia.org/wiki/Curing_(chemistry)'),
                                ('Gel point', 'https://en.wikipedia.org/wiki/Gel_point'),
                                ('Percolation theory', 'https://en.wikipedia.org/wiki/Percolation_theory')]},
 'Adhesive: static mixer (2K)': {'what': 'The mixing nozzle of a two-component adhesive cartridge holds a '
                                         'static mixer: a row of twisted elements. Each element cuts the '
                                         'flow of resin and hardener in two, stacks the halves and turns '
                                         'them by 90°. So the number of layers doubles with every element: '
                                         '2, 4, 8, 16 … After about ten elements the layers are so thin that '
                                         'the two components react everywhere. Here each generation is one '
                                         'mixer element: the resin is one colour, the hardener the other.',
                                 'watch': 'Count the layers in the line above the grid: they double with '
                                          'every element. When each layer is only one cell thin, the '
                                          'adhesive is mixed. Too few elements (a nozzle cut too short) '
                                          'leave thick stripes: those spots would stay soft.',
                                 'try': 'Resin above, hardener below, then one element per half second.',
                                 'setup': {'mixer': True, 'speed': 2},
                                 'links': [('Static mixer', 'https://en.wikipedia.org/wiki/Static_mixer'),
                                           ('Adhesive', 'https://en.wikipedia.org/wiki/Adhesive')]},
 'own': {'what': 'Write a rule of your own as B…/S…: after B the numbers of live neighbours (0–8) that bring '
                 'a dead cell to life, after S those that keep a live cell alive. Add /C and a number for a '
                 'rule with dying states, such as B2/S/C3. The rule can be changed while it runs.',
         'watch': 'Few numbers after B and many after S make it grow slowly and settle; many after B make it '
                  'explode.',
         'try': 'Type B3/S1234 in the field, Use rule, and Random.',
         'setup': {'rule': 'B3/S1234', 'density': 0.3},
         'links': [('Life-like cellular automaton',
                    'https://en.wikipedia.org/wiki/Life-like_cellular_automaton'),
                   ('Cellular automaton', 'https://en.wikipedia.org/wiki/Cellular_automaton')]}}

PATTERN_INFO = {'draw': {'what': 'Click a dead cell to bring it to life and a live one to kill it; drag to draw lines. The '
                  'right mouse button erases. Draw while it runs, too.',
          'watch': 'What you draw starts green (just born).',
          'try': 'Clear, draw three cells in a row, and Play: a blinker.',
          'setup': {'pattern': 'Blinker'},
          'links': [('Cellular automaton', 'https://en.wikipedia.org/wiki/Cellular_automaton'),
                    ('Oscillator', 'https://en.wikipedia.org/wiki/Oscillator_(cellular_automaton)')]},
 'Glider': {'what': 'Five cells that move one cell diagonally every 4 generations: the smallest spaceship of '
                    'Life. It flies until it hits something; with wrapping edges it comes back on the other '
                    'side.',
            'watch': 'It stays young (yellow and orange) because its cells keep being born and dying, and '
                     'leaves a red trace.',
            'try': 'One glider on an empty grid.',
            'setup': {'rule': 'B3/S23', 'pattern': 'Glider', 'wrap': True},
            'links': [('Glider', 'https://en.wikipedia.org/wiki/Glider_(Conway%27s_Game_of_Life)')]},
 'Lightweight spaceship': {'what': 'A spaceship that flies straight sideways, two cells every 4 generations. '
                                   'Left alone it flies forever.',
                           'watch': 'The small sparks behind it appear and vanish at once: red traces right '
                                    'behind it.',
                           'try': 'One spaceship on an empty grid.',
                           'setup': {'rule': 'B3/S23', 'pattern': 'Lightweight spaceship', 'wrap': True},
                           'links': [('Spaceship',
                                      'https://en.wikipedia.org/wiki/Spaceship_(cellular_automaton)')]},
 'Pulsar': {'what': 'The best-known oscillator: it repeats every 3 generations, pulsing the same way in all '
                    'four directions.',
            'watch': 'Because its cells are born again and again, it never grows old: it stays green and '
                     'yellow.',
            'try': 'One pulsar on an empty grid, step by step.',
            'setup': {'rule': 'B3/S23', 'pattern': 'Pulsar', 'play': False},
            'links': [('Oscillator', 'https://en.wikipedia.org/wiki/Oscillator_(cellular_automaton)')]},
 'Gosper glider gun': {'what': 'The first pattern found that grows forever (1970): a gun that shoots a new '
                               'glider every 30 generations. With wrapping edges its gliders come back and '
                               'destroy it in the end.',
                       'watch': 'The two blocks at its ends stay violet (they stand still); the gun between '
                                'them and the gliders stay young.',
                       'try': 'The gun on an empty grid with Edges wrap off, so it can shoot forever.',
                       'setup': {'rule': 'B3/S23', 'pattern': 'Gosper glider gun', 'wrap': False},
                       'links': [('Gun', 'https://en.wikipedia.org/wiki/Gun_(cellular_automaton)'),
                                 ('Bill Gosper', 'https://en.wikipedia.org/wiki/Bill_Gosper')]},
 'R-pentomino': {'what': 'Only five cells, but a "methuselah": it boils for 1103 generations, throws out six '
                         'gliders and leaves still lifes and blinkers behind.',
                 'watch': 'Watch the population line: it rises and falls for a long time before it lies '
                          'flat.',
                 'try': 'The R-pentomino on an empty grid with Edges wrap off, at a high speed.',
                 'setup': {'rule': 'B3/S23', 'pattern': 'R-pentomino', 'wrap': False},
                 'links': [('Methuselah', 'https://en.wikipedia.org/wiki/Methuselah_(cellular_automaton)')]},
 'Acorn': {'what': 'Seven cells that take 5206 generations to settle, growing to over 600 cells on the way: '
                   'one of the longest-lived small seeds.',
           'watch': 'More and more violet islands where parts of it have settled, while the rest keeps '
                    'boiling.',
           'try': 'The acorn on an empty grid with Edges wrap off, at a high speed.',
           'setup': {'rule': 'B3/S23', 'pattern': 'Acorn', 'wrap': False},
           'links': [('Methuselah', 'https://en.wikipedia.org/wiki/Methuselah_(cellular_automaton)')]},
 'Diehard': {'what': 'Seven cells that live for exactly 130 generations and then vanish completely.',
             'watch': 'Count the generations: at 130 the last cells turn red and the grid is empty.',
             'try': 'The diehard on an empty grid with Edges wrap off.',
             'setup': {'rule': 'B3/S23', 'pattern': 'Diehard', 'wrap': False},
             'links': [('Methuselah', 'https://en.wikipedia.org/wiki/Methuselah_(cellular_automaton)')]}}
