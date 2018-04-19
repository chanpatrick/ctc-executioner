import logging
import copy
import random
import numpy as np
import gym
from gym import error, spaces, utils
from gym.utils import seeding
from action import Action
from action_state import ActionState
from order import Order
from order_type import OrderType
from order_side import OrderSide

#logging.basicConfig(level=logging.INFO)

class ExecutionEnv(gym.Env):

    def __init__(self):
        self.orderbookIndex = None
        self.actionState = None
        self.execution = None
        self._configure()

    def _generate_Sequence(self, min, max, step):
        """ Generate sequence (that unlike xrange supports float)

        max: defines the sequence maximum
        step: defines the interval
        """
        i = min
        I = []
        while i <= max:
            I.append(i)
            i = i + step
        return I

    def _configure(self,
                   orderbook=None,
                   side=OrderSide.SELL,
                   levels=(-50, 50, 1),
                   T=(0, 100, 10),
                   I=(0, 1, 0.1),
                   lookback=25,
                   bookSize=10
                   ):
        self.orderbook = orderbook
        self.side = OrderSide.SELL
        self.levels = self._generate_Sequence(min=levels[0], max=levels[1], step=levels[2])
        self.T = self._generate_Sequence(min=T[0], max=T[1], step=T[2])
        self.I = self._generate_Sequence(min=I[0], max=I[1], step=I[2])
        self.lookback = lookback # results in (bid|size, ask|size) -> 4*5
        self.bookSize = bookSize
        self.action_space = spaces.Discrete(len(self.levels))
        self.observation_space = spaces.Box(low=0.0, high=10.0, shape=(2*self.lookback, self.bookSize, 2))

    def setOrderbook(self, orderbook):
        self.orderbook = orderbook

    def setSide(self, side):
        self.side = side

    def setLevels(self, min, max, step):
        self.levels = self._generate_Sequence(min=min, max=max, step=step)
        self.action_space = spaces.Discrete(len(self.levels))

    def setT(self, min, max, step):
        self.T = self._generate_Sequence(min=min, max=max, step=step)

    def setI(self, min, max, step):
        self.I = self._generate_Sequence(min=min, max=max, step=step)

    def setLookback(self, lookback):
        self.lookback = lookback
        if self.bookSize is not None:
            self.observation_space = spaces.Box(low=0.0, high=10.0, shape=(2*self.lookback, self.bookSize, 2))

    def setBookSize(self, bookSize):
        self.bookSize = bookSize
        if self.lookback is not None:
            self.observation_space = spaces.Box(low=0.0, high=10.0, shape=(2*self.lookback, self.bookSize, 2))



    def _determine_next_inventory(self, execution):
        qty_remaining = execution.getQtyNotExecuted()
        # TODO: Working with floats requires such an ugly threshold
        if qty_remaining > 0.0000001:
            # Approximate next closest inventory given remaining and I
            i_next = min([0.0] + self.I, key=lambda x: abs(x - qty_remaining))
            logging.info('Qty remain: ' + str(qty_remaining)
                         + ' -> inventory: ' + str(qty_remaining)
                         + ' -> next i: ' + str(i_next))
        else:
            i_next = 0.0

        logging.info('Next inventory for execution: ' + str(i_next))
        return i_next

    def _determine_next_time(self, t):
        if t > 0:
            t_next = self.T[self.T.index(t) - 1]
        else:
            t_next = t

        logging.info('Next timestep for execution: ' + str(t_next))
        return t_next

    def _determine_runtime(self, t):
        if t != 0:
            T_index = self.T.index(t)
            runtime = self.T[T_index] - self.T[T_index - 1]
        else:
            runtime = t
        return runtime

    def _get_random_orderbook_state(self):
        return self.orderbook.getRandomState(runtime=max(self.T), min_head=self.lookback)

    def _create_execution(self, a):
        runtime = self._determine_runtime(self.actionState.getT())
        orderbookState = self.orderbook.getState(self.orderbookIndex)

        if runtime <= 0.0 or a is None:
            price = None
            ot = OrderType.MARKET
        else:
            price = orderbookState.getPriceAtLevel(self.side, a)
            ot = OrderType.LIMIT

        order = Order(
            orderType=ot,
            orderSide=self.side,
            cty=self.actionState.getI(),
            price=price
        )
        execution = Action(a=a, runtime=runtime)
        execution.setState(self.actionState)
        execution.setOrder(order)
        execution.setOrderbookState(orderbookState)
        execution.setOrderbookIndex(self.orderbookIndex)
        execution.setReferencePrice(orderbookState.getBestAsk())
        return execution

    def _update_execution(self, execution, a):
        runtime = self._determine_runtime(self.actionState.getT())
        orderbookState = self.orderbook.getState(self.orderbookIndex)

        if runtime <= 0.0 or a is None:
            price = None
            ot = OrderType.MARKET
        else:
            price = execution.getOrderbookState().getPriceAtLevel(self.side, a)
            ot = OrderType.LIMIT

        order = Order(
            orderType=ot,
            orderSide=self.side,
            cty=self.actionState.getI(),
            price=price
        )
        execution.setRuntime(runtime)
        execution.setState(self.actionState)
        execution.setOrder(order)
        execution.setOrderbookState(orderbookState)
        execution.setOrderbookIndex(self.orderbookIndex)
        return execution

    def _makeFeature(self, orderbookIndex):
        return self.orderbook.getBidAskFeatures(
            state_index=orderbookIndex,
            lookback=self.lookback,
            qty=self.I[-1],#i_next+0.0001,
            normalize=True,
            price=True,
            size=True,
            levels = self.bookSize
        )

    def step(self, action):
        action = self.levels[action]
        if self.execution is None:
            self.execution = self._create_execution(action)
        else:
            self.execution = self._update_execution(self.execution, action)

        logging.info(
            'Created/Updated execution.' +
            '\nAction: ' + str(action) + ' (' + str(self.execution.getOrder().getType()) + ')' +
            '\nt: ' + str(self.actionState.getT()) +
            '\nruntime: ' + str(self.execution.getRuntime()) +
            '\ni: ' + str(self.actionState.getI())
        )
        self.execution, counterTrades = self.execution.run(copy.deepcopy(self.orderbook)) # TODO: Slow but currently required due to change of order book states during matching

        i_next = self._determine_next_inventory(self.execution)
        t_next = self._determine_next_time(self.execution.getState().getT())

        bidAskFeature = self._makeFeature(orderbookIndex=self.execution.getOrderbookIndex())
        state_next = ActionState(t_next, i_next, {'bidask': bidAskFeature})
        done = self.execution.isFilled() or state_next.getI() == 0
        # if done == True:
        #     #reward = self.execution.getReward()
        #     #volumeRatio = 1.0
        # else:
        reward, volumeRatio = self.execution.calculateRewardWeighted(counterTrades, self.I[-1])

        logging.info(
            'Run execution.' +
            '\nTrades: ' + str(len(counterTrades)) +
            '\nReward: ' + str(reward) + ' (Ratio: ' + str(volumeRatio) + ')' +
            '\nDone: ' + str(done)
        )
        self.orderbookIndex = self.execution.getOrderbookIndex()
        self.actionState = state_next
        return state_next.toArray(), reward, done, {}

    def reset(self):
        return self._reset(t=self.T[-1], i=self.I[-1])

    def _reset(self, t, i):
        orderbookState, orderbookIndex = self._get_random_orderbook_state()
        bidAskFeature = self._makeFeature(orderbookIndex=orderbookIndex)
        state = ActionState(t, i, {'bidask': bidAskFeature}) #np.array([[t, i]])
        self.execution = None
        self.orderbookIndex = orderbookIndex
        self.actionState = state
        return state.toArray()

    def render(self, mode='human', close=False):
        pass

    def seed(self, seed):
        pass


# import gym_ctc_executioner
# env = gym.make("ctc-executioner-v0")
# env.reset()
