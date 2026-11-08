import io
import json
from dataclasses import replace
from pathlib import Path
import unittest
from sim.models import Config, Trip, SimulationError
from sim.run_inputs import RunInputs, expand
from sim.events import MemorySink, JsonlSink
from sim.engine import simulate
from sim.schedule import validate_schedule
from sim.dispatcher import Dispatcher
from sim.adapters.opt_adapter import prepare_inputs, compile_schedule, assessment, evaluation_port, service_plan
from opt.models import load_inputs, make_plan
from opt.integration import checked_assessment, rerank
from opt.search import search

ROOT = Path(__file__).resolve().parents[2]

class SimulationTests(unittest.TestCase):
    def setUp(self):
        f = json.loads((ROOT/'sim/fixtures/direct_shuttle.json').read_text())
        self.run = RunInputs('bundle','scenario','source',f['service_date'],
            expand([('d','A','B',f['arrival_s'],f['passenger_count'])]), (('ab',5),('ba',5)), 'fixture')
        self.config = Config(f['max_wait_s'], f['max_journey_s'], f['drain_s'], 'fifo','fixed_segments')
        self.trips = tuple(Trip(str(i),'v',f['capacity'],('A','B'),(t,t+5),(t,t+5),t+10,100)
                           for i,t in enumerate(f['departures']))
    def execute(self, **changes):
        sink = MemorySink()
        return simulate('plan',1,self.run,self.trips,replace(self.config,**changes),sink), sink.events
    def test_hand_calculation(self):
        r, events = self.execute()
        self.assertEqual(r.served,3)
        self.assertEqual([s.boarding_s for s in r.passengers],[0,0,10])
        self.assertEqual([s.arrival_s for s in r.passengers],[5,5,15])
        self.assertEqual(r.p90_wait_s,10)
        self.assertEqual(len(self.run.passengers),3)
        self.assertTrue(all(e.occupancy is None or 0<=e.occupancy<=2 for e in events))
        self.assertEqual([e.time_s for e in events],sorted(e.time_s for e in events))
        self.assertTrue(all((s.passenger.origin,s.passenger.destination)==('A','B') for s in r.passengers))
        self.assertEqual(r.served+r.unserved+r.censored,r.total)
    def test_wait_deadline(self):
        r,_ = self.execute(max_wait_s=9)
        self.assertEqual((r.served,r.unserved),(2,1))
        self.assertIsNone(r.passengers[-1].boarding_s)
    def test_journey_physical_arrival_retained(self):
        r,_ = self.execute(max_journey_s=14)
        self.assertEqual((r.served,r.unserved),(2,1))
        self.assertEqual(r.passengers[-1].arrival_s,15)
        self.assertEqual(r.passengers[-1].physical,'arrived')
    def test_censored(self):
        r,_ = self.execute(drain_s=12)
        self.assertEqual((r.served,r.censored),(2,1))
        self.assertEqual(r.passengers[-1].physical,'onboard')
    def test_reproducibility(self):
        self.assertEqual(self.execute(),self.execute())
        stream=io.StringIO()
        simulate('plan',1,self.run,self.trips,self.config,JsonlSink(stream))
        self.assertEqual(len(stream.getvalue().splitlines()),self.execute()[0].event_count)
    def test_invalid_inputs_and_schedule(self):
        for trips in (self.trips+self.trips, (replace(self.trips[0],capacity=0),),
                      (self.trips[0],replace(self.trips[1],arrivals=(9,14),departures=(9,14)))):
            with self.assertRaises(SimulationError): validate_schedule(trips)
        with self.assertRaises(SimulationError): replace(self.run,passengers=self.run.passengers*2)
        with self.assertRaises(SimulationError): replace(self.run,travel_times=(('ab',0),))
        with self.assertRaises(SimulationError): replace(self.config,max_wait_s=True)
        with self.assertRaises(BufferError):
            simulate('plan',1,self.run,self.trips,self.config,MemorySink(1))
    def test_dispatcher_order(self):
        d, seen = Dispatcher(), []
        keys=[(0,p,s) for p in range(5) for s in ('a','b')]
        for key in reversed(keys): d.add(*key,lambda *k:seen.append(k))
        d.run(0)
        self.assertEqual(seen,keys)
        with self.assertRaises(SimulationError): d.add(0,0,'a',lambda *k:None)

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.x=load_inputs(ROOT/'opt/examples/synthetic.json')
        self.p=make_plan(self.x,{'r1':1},'m05-test')
        self.run=prepare_inputs(self.x,seed='fixed')
        self.config=Config(self.x.policy.max_wait_s,self.x.policy.max_travel_s,1000,'fifo','fixed_segments')
    def test_port_and_schedule(self):
        trips=compile_schedule(self.p,self.x,self.run)
        validate_schedule(trips)
        self.assertTrue(trips)
        output=service_plan(self.p,self.x,self.run)
        self.assertEqual(output['schedule_status'],'compiled')
        self.assertTrue(output['vehicle_duties'])
        port=evaluation_port(self.run,self.config)
        result=checked_assessment(port,self.p,self.x)
        self.assertFalse(result.independently_verified)
        self.assertEqual(result.total,self.x.total_demand)
        self.assertIsNone(rerank(self.x,search(self.x).top,port).recommended)
    def test_realization_changes_rejected(self):
        for run in (replace(self.run,source_checksum='wrong'),
                    replace(self.run,travel_times=tuple((k,v+1) for k,v in self.run.travel_times)),
                    replace(self.run,passengers=self.run.passengers[:-1])):
            with self.assertRaises(ValueError): compile_schedule(self.p,self.x,run)
    def test_result_mismatch(self):
        trips=compile_schedule(self.p,self.x,self.run)
        result=simulate(self.p.plan_id,self.p.revision,self.run,trips,self.config)
        for changes in ({'plan_id':'wrong'},{'plan_revision':99},{'source_checksum':'wrong'},
                        {'input_bundle_id':'wrong'},{'passengers':result.passengers[:-1]}):
            with self.assertRaises(ValueError): assessment(self.p,self.x,replace(result,**changes))
    def test_compiled_schedule_obeys_availability(self):
        trips=compile_schedule(self.p,self.x,self.run)
        for t in trips:
            self.assertLessEqual(t.return_s,self.x.policy.end_s)
            self.assertGreaterEqual(t.arrivals[0],self.x.policy.start_s)

if __name__=='__main__': unittest.main()
