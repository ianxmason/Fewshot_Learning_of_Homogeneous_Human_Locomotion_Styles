import sys
import numpy as np
import theano
import theano.tensor as T
import time
import pickle
theano.config.allow_gc = True
sys.path.append('./nn')
from Layer import Layer
from HiddenLayer import HiddenLayer
from BiasLayer import BiasLayer
from DropoutLayer import DropoutLayer
from ActivationLayer import ActivationLayer
from AdamTrainer import AdamTrainer
from DiagLayer import DiagLayer

""" 
Few-shot training of the residual adapters decomposed using CP decomposition and with the central diagonal tensor of size 30x30.
"""

mname='CP'
rng = np.random.RandomState(23456)

""" Load Data """

database = np.load('./Data/fewshot_database.npz')
X_default = database['Xin'].astype(theano.config.floatX)
Y_default = database['Yin'].astype(theano.config.floatX)
P_default = database['Pin'].astype(theano.config.floatX)
X_mirror = database['Xin_mirror'].astype(theano.config.floatX)
Y_mirror = database['Yin_mirror'].astype(theano.config.floatX)
P_mirror = database['Pin_mirror'].astype(theano.config.floatX)

j = 31
w = ((60*2)//10)

L_default = np.copy(X_default[:,w*6:w*56]).astype(theano.config.floatX)  # 50 styles
L_mirror = np.copy(X_mirror[:,w*6:w*56]).astype(theano.config.floatX)
X_default = np.concatenate((X_default[:,w*0:w*4], X_default[:,w*56:]), axis=1).astype(theano.config.floatX)
X_mirror = np.concatenate((X_mirror[:,w*0:w*4], X_mirror[:,w*56:]), axis=1).astype(theano.config.floatX)

X = np.concatenate((X_default, X_mirror), axis=0)
Y = np.concatenate((Y_default, Y_mirror), axis=0)
P = np.concatenate((P_default, P_mirror), axis=0)
L = np.concatenate((L_default, L_mirror), axis=0)

""" Mask Out Unused Joints in Input """

joint_weights = np.array([
    1,
    1e-10, 1, 1, 1, 1,
    1e-10, 1, 1, 1, 1,
    1e-10, 1, 1,
    1e-10, 1, 1,
    1e-10, 1, 1, 1, 1e-10, 1e-10, 1e-10,
    1e-10, 1, 1, 1, 1e-10, 1e-10, 1e-10]).repeat(3)

""" Load Mean / Std / Min / Max """

Xmean = np.fromfile('./Parameters/' + mname + '/Xmean.bin', dtype=np.float32)
Ymean = np.fromfile('./Parameters/' + mname + '/Ymean.bin', dtype=np.float32)
Xstd = np.fromfile('./Parameters/' + mname + '/Xstd.bin', dtype=np.float32)
Ystd = np.fromfile('./Parameters/' + mname + '/Ystd.bin', dtype=np.float32)

""" Normalise by precalculated mean and std """

X_default = (X_default - Xmean) / Xstd
Y_default = (Y_default - Ymean) / Ystd
X_mirror = (X_mirror - Xmean) / Xstd
Y_mirror = (Y_mirror - Ymean) / Ystd

""" Fewshot Styles List"""

styletransfer_styles = [
    'Balance', 'BentForward', 'BentKnees', 'Bouncy', 'Cat', 'Chicken', 'Cool',
    'Crossover', 'Crouched', 'Dance3', 'Dinosaur', 'DragLeg', 'Drunk',
    'DuckFoot', 'Elated', 'Frankenstein', 'Gangly',
    'Gedanbarai', 'Graceful', 'Heavyset', 'Heiansyodan', 'Hobble',
    'HurtLeg', 'Jaunty', 'Joy', 'LeanRight', 'LeftHop', 'LegsApart', 'Mantis',
    'March', 'Mawashigeri', 'OnToesBentForward', 'OnToesCrouched',
    'PainfulLeftknee', 'Penguin', 'PigeonToed', 'PrarieDog', 'Quail', 'Roadrunner',
    'Rushed', 'Sneaky', 'Squirrel', 'Stern', 'Stuff',
    'SwingShoulders', 'WildArms', 'WildLegs', 'WoundedLeg',
    'Yokogeri', 'Zombie']

""" Phase Function Neural Network """

class PhaseFunctionedNetwork(Layer):
    
    def __init__(self, rng=rng, input_shape=1, output_shape=1, dropout=0.7, dropout_res=0.5, style='Balance', batchsize=20):
        
        self.style = style
        self.batchsize = batchsize
        self.nslices = 4        
        self.dropout0 = DropoutLayer(dropout, rng=rng)
        self.dropout1 = DropoutLayer(dropout, rng=rng)
        self.dropout_res = DropoutLayer(dropout_res, rng=rng)
        self.dropout2 = DropoutLayer(dropout, rng=rng)
        self.activation = ActivationLayer('ELU')
        
        W0_load = np.empty((self.nslices, 512, input_shape-1), dtype=np.float32)
        W1_load = np.empty((self.nslices, 512, 512), dtype=np.float32)
        W2_load = np.empty((self.nslices, output_shape, 512), dtype=np.float32)

        b0_load = np.empty((self.nslices, 512), dtype=np.float32)
        b1_load = np.empty((self.nslices, 512), dtype=np.float32)
        b2_load = np.empty((self.nslices, output_shape), dtype=np.float32)

        for i in range(4): 
            W0_load[i] = np.fromfile('./Parameters/' + mname + '/W0_%03i.bin' % (int)(i * 12.5), dtype=np.float32).reshape(512, input_shape-1)
            W1_load[i] = np.fromfile('./Parameters/' + mname + '/W1_%03i.bin' % (int)(i * 12.5), dtype=np.float32).reshape(512, 512)
            W2_load[i] = np.fromfile('./Parameters/' + mname + '/W2_%03i.bin' % (int)(i * 12.5), dtype=np.float32).reshape(output_shape, 512)

            b0_load[i] = np.fromfile('./Parameters/' + mname + '/b0_%03i.bin' % (int)(i * 12.5), dtype=np.float32)
            b1_load[i] = np.fromfile('./Parameters/' + mname + '/b1_%03i.bin' % (int)(i * 12.5), dtype=np.float32)
            b2_load[i] = np.fromfile('./Parameters/' + mname + '/b2_%03i.bin' % (int)(i * 12.5), dtype=np.float32)

        self.W0 = HiddenLayer((self.nslices, 512, input_shape-1), rng=rng, gamma=0.01)
        self.W1 = HiddenLayer((self.nslices, 512, 512), rng=rng, gamma=0.01)
        self.W2 = HiddenLayer((self.nslices, output_shape, 512), rng=rng, gamma=0.01)
    
        self.b0 = BiasLayer((self.nslices, 512))
        self.b1 = BiasLayer((self.nslices, 512))
        self.b2 = BiasLayer((self.nslices, output_shape))

        self.W0.W.set_value(W0_load)
        self.W1.W.set_value(W1_load)
        self.W2.W.set_value(W2_load)

        self.b0.b.set_value(b0_load)
        self.b1.b.set_value(b1_load)
        self.b2.b.set_value(b2_load)

        self.style_W0 = HiddenLayer((1, 30, 512), rng=rng, gamma=0.01)
        self.style_W1 = DiagLayer((self.nslices, 1, 30), rng=rng, gamma=0.01)
        self.style_W2 = HiddenLayer((1, 512, 30), rng=rng, gamma=0.01)
        self.style_b = BiasLayer((1, 512))

        self.layers = [self.style_W0, self.style_W1, self.style_W2, self.style_b]

        self.params = sum([layer.params for layer in self.layers], []) # The only parameters we want to update are the residual adapter ones

        style_label = np.zeros(L.shape[1])
        style_label[w*styletransfer_styles.index(self.style):w*(styletransfer_styles.index(self.style)+1)] = 1
        self.style_label = theano.shared(style_label, borrow=True)

        zeros = np.zeros((1, output_shape))
        self.zeros = T.addbroadcast(theano.shared(zeros, borrow=True), 0)
        
    def __call__(self, input):
        
        pscale = self.nslices * input[:,-1]
        pamount = pscale % 1.0
        
        pindex_1 = T.cast(pscale, 'int32') % self.nslices
        pindex_0 = (pindex_1-1) % self.nslices
        pindex_2 = (pindex_1+1) % self.nslices
        pindex_3 = (pindex_1+2) % self.nslices
        
        Wamount = pamount.dimshuffle(0, 'x', 'x')
        bamount = pamount.dimshuffle(0, 'x')
        
        def cubic(y0, y1, y2, y3, mu):
            return (
                (-0.5*y0+1.5*y1-1.5*y2+0.5*y3)*mu*mu*mu + 
                (y0-2.5*y1+2.0*y2-0.5*y3)*mu*mu + 
                (-0.5*y0+0.5*y2)*mu +
                (y1))
        
        W0 = cubic(self.W0.W[pindex_0], self.W0.W[pindex_1], self.W0.W[pindex_2], self.W0.W[pindex_3], Wamount)
        W1 = cubic(self.W1.W[pindex_0], self.W1.W[pindex_1], self.W1.W[pindex_2], self.W1.W[pindex_3], Wamount)
        W2 = cubic(self.W2.W[pindex_0], self.W2.W[pindex_1], self.W2.W[pindex_2], self.W2.W[pindex_3], Wamount)
        
        b0 = cubic(self.b0.b[pindex_0], self.b0.b[pindex_1], self.b0.b[pindex_2], self.b0.b[pindex_3], bamount)
        b1 = cubic(self.b1.b[pindex_0], self.b1.b[pindex_1], self.b1.b[pindex_2], self.b1.b[pindex_3], bamount)
        b2 = cubic(self.b2.b[pindex_0], self.b2.b[pindex_1], self.b2.b[pindex_2], self.b2.b[pindex_3], bamount)
        
        # new residual adapter weights
        style_W1 = cubic(self.style_W1.W[pindex_0], self.style_W1.W[pindex_1], self.style_W1.W[pindex_2], self.style_W1.W[pindex_3], Wamount)

        sty_index = T.cast(theano.shared(np.zeros((self.batchsize,))), 'int32')
        style_W0 = self.style_W0.W[sty_index]
        style_W2 = self.style_W2.W[sty_index]
        style_b = self.style_b.b[sty_index]

        H0 = input[:,:-1]
        H1 = self.activation(T.batched_dot(W0, self.dropout0(H0)) + b0)
        
        style_H3 = T.batched_dot(W2, self.dropout2(self.activation(T.batched_dot(W1, self.dropout1(H1)) + b1 + T.batched_dot(style_W2, (style_W1.reshape([self.batchsize,30]) * T.batched_dot(style_W0, self.dropout1(H1)))) + style_b))) + b2

        return style_H3
        
    def cost(self, input):
        input = input[:,:-1]
        costs = 0
        for layer in self.layers:
            costs += layer.cost(input)
            input = layer(input)
        return costs / len(self.layers)
    
    def save(self, database, prefix=''):
        for li, layer in enumerate(self.layers):
            layer.save(database, '%sL%03i_' % (prefix, li))
        
    def load(self, database, prefix=''):
        for li, layer in enumerate(self.layers):
            layer.load(database, '%sL%03i_' % (prefix, li))

            
""" Function to Save Network Weights """

def save_network(network):

    """ Load Control Points """

    style_W0 = network.style_W0.W.get_value()  
    style_W1n = network.style_W1.W.get_value()
    style_W2 = network.style_W2.W.get_value()
    style_b = network.style_b.b.get_value()

    """ Precompute Phase Function """
    
    for i in range(50):
        
        pscale = network.nslices*(float(i)/50)
        pamount = pscale % 1.0
        
        pindex_1 = int(pscale) % network.nslices
        pindex_0 = (pindex_1-1) % network.nslices
        pindex_2 = (pindex_1+1) % network.nslices
        pindex_3 = (pindex_1+2) % network.nslices
        
        def cubic(y0, y1, y2, y3, mu):
            return (
                (-0.5*y0+1.5*y1-1.5*y2+0.5*y3)*mu*mu*mu + 
                (y0-2.5*y1+2.0*y2-0.5*y3)*mu*mu + 
                (-0.5*y0+0.5*y2)*mu +
                (y1))

        style_W1 = cubic(style_W1n[pindex_0], style_W1n[pindex_1], style_W1n[pindex_2], style_W1n[pindex_3], pamount)
        fnameW1 = './Parameters/' + mname + '/Fewshot/' + network.style + ('_W1_%03i.bin' % i)        
        style_W1.astype(np.float32).tofile(fnameW1)
        
    fnameW0 = './Parameters/' + mname + '/Fewshot/' + network.style + ('_W0.bin') 
    fnameW2 = './Parameters/' + mname + '/Fewshot/' + network.style + ('_W2.bin') 
    fnameb = './Parameters/' + mname + '/Fewshot/' + network.style + ('_b.bin')
    style_W0[0].astype(np.float32).tofile(fnameW0)
    style_W2[0].astype(np.float32).tofile(fnameW2)
    style_b[0].astype(np.float32).tofile(fnameb)


main_drop = {
    'Balance':0.5, 'BentForward':0.7, 'BentKnees':0.7, 'Bouncy':0.7, 'Cat':0.7, 'Chicken':0.7, 'Cool':0.7,
    'Crossover':0.7, 'Crouched':0.7, 'Dance3':0.7, 'Dinosaur':0.7, 'DragLeg':0.7, 'Drunk':0.7,
    'DuckFoot':0.7, 'Elated':0.7, 'Frankenstein':0.7, 'Gangly':0.7,
    'Gedanbarai':0.7, 'Graceful':0.7, 'Heavyset':0.7, 'Heiansyodan':0.7, 'Hobble':0.7,
    'HurtLeg':0.7, 'Jaunty':0.7, 'Joy':0.7, 'LeanRight':0.7, 'LeftHop':0.7, 'LegsApart':0.7, 'Mantis':0.7,
    'March':0.7, 'Mawashigeri':0.7, 'OnToesBentForward':0.7, 'OnToesCrouched':0.7,
    'PainfulLeftknee':0.7, 'Penguin':0.7, 'PigeonToed':0.7, 'PrarieDog':0.7, 'Quail':0.7, 'Roadrunner':0.7,
    'Rushed':0.7, 'Sneaky':0.7, 'Squirrel':0.7, 'Stern':0.7, 'Stuff':0.7,
    'SwingShoulders':0.7, 'WildArms':0.7, 'WildLegs':0.7, 'WoundedLeg':0.7,
    'Yokogeri':0.7, 'Zombie':0.7}  # Main drop affects how closely the style matches the training data when given a similar input

res_drop = {
    'Balance':0.5, 'BentForward':0.5, 'BentKnees':0.5, 'Bouncy':0.5, 'Cat':0.5, 'Chicken':0.3, 'Cool':0.5,
    'Crossover':0.3, 'Crouched':0.5, 'Dance3':0.3, 'Dinosaur':0.3, 'DragLeg':0.5, 'Drunk':0.3,
    'DuckFoot':0.5, 'Elated':0.5, 'Frankenstein':0.5, 'Gangly':0.5,
    'Gedanbarai':0.3, 'Graceful':0.3, 'Heavyset':0.7, 'Heiansyodan':0.3, 'Hobble':0.5,
    'HurtLeg':0.5, 'Jaunty':0.5, 'Joy':0.5, 'LeanRight':0.5, 'LeftHop':0.3, 'LegsApart':0.5, 'Mantis':0.5,
    'March':0.3, 'Mawashigeri':0., 'OnToesBentForward':0.3, 'OnToesCrouched':0.3,
    'PainfulLeftknee':0.5, 'Penguin':0.3, 'PigeonToed':0.5, 'PrarieDog':0.5, 'Quail':0.3, 'Roadrunner':0.5,
    'Rushed':0.5, 'Sneaky':0.3, 'Squirrel':0.5, 'Stern':0.5, 'Stuff':0.3,
    'SwingShoulders':0.7, 'WildArms':0.5, 'WildLegs':0.3, 'WoundedLeg':0.3,
    'Yokogeri':0.3, 'Zombie':0.3}  # Res drop affects generalisation when given new input (direction/speed)  

time_dict = {}    
loss_dict = {}    
no_of_clips_default = np.sum(L_default, axis=0)[::w]   # no of clips in each style
no_of_clips_mirror = np.sum(L_mirror, axis=0)[::w]   
no_cumsum_default = np.insert(np.cumsum(no_of_clips_default),0,0)
no_cumsum_mirror = np.insert(np.cumsum(no_of_clips_mirror),0,0)
for i, style in enumerate(styletransfer_styles):

    """ Train on each Fewshot style individually """

    print "\n Training on style " + style + (" with %i non-mirrored clips" % int(no_of_clips_default[i]))

    batchsize = 32 # int(no_of_clips[i])
    epochs = 100

    """ Construct Network """

    print "Constructing Network..."
    network = PhaseFunctionedNetwork(rng=rng, input_shape=X.shape[1]+1, output_shape=Y.shape[1], dropout=main_drop[style], dropout_res=res_drop[style], style=style, batchsize=batchsize)

    """ Construct Trainer """    

    trainer = AdamTrainer(rng=rng, batchsize=batchsize, epochs=epochs, alpha=0.0001)

    """ Shuffle all data to avoid any problems caused by capturing similar motions together. """

    X_in_default = X_default[int(no_cumsum_default[i]):int(no_cumsum_default[i+1])]
    Y_in_default = Y_default[int(no_cumsum_default[i]):int(no_cumsum_default[i+1])]
    L_in_default = L_default[int(no_cumsum_default[i]):int(no_cumsum_default[i+1])]
    P_in_default = P_default[int(no_cumsum_default[i]):int(no_cumsum_default[i+1])]
    X_in_mirror = X_mirror[int(no_cumsum_mirror[i]):int(no_cumsum_mirror[i+1])]
    Y_in_mirror = Y_mirror[int(no_cumsum_mirror[i]):int(no_cumsum_mirror[i+1])]
    L_in_mirror = L_mirror[int(no_cumsum_mirror[i]):int(no_cumsum_mirror[i+1])]
    P_in_mirror = P_mirror[int(no_cumsum_mirror[i]):int(no_cumsum_mirror[i+1])]

    if len(X_in_default) == len(X_in_mirror):
        print "Symmetric Style"
        I=np.arange(len(X_in_default))
        rng.shuffle(I)
        X_in_default = X_in_default[I]
        Y_in_default = Y_in_default[I]
        L_in_default = L_in_default[I]
        P_in_default = P_in_default[I]
        X_in_mirror = X_in_mirror[I]
        Y_in_mirror = Y_in_mirror[I]
        L_in_mirror = L_in_mirror[I]
        P_in_mirror = P_in_mirror[I]

        X_train = []
        Y_train = []
        L_train = []
        P_train = []
        for j in xrange(len(X_in_default)):
            X_train.append(np.expand_dims(X_in_default[j], axis=0))
            X_train.append(np.expand_dims(X_in_mirror[j], axis=0))
            Y_train.append(np.expand_dims(Y_in_default[j], axis=0))
            Y_train.append(np.expand_dims(Y_in_mirror[j], axis=0))
            L_train.append(np.expand_dims(L_in_default[j], axis=0))
            L_train.append(np.expand_dims(L_in_mirror[j], axis=0))
            P_train.append(np.expand_dims(P_in_default[j], axis=0))
            P_train.append(np.expand_dims(P_in_mirror[j], axis=0))

        X_in = np.concatenate(X_train, axis=0)
        Y_in = np.concatenate(Y_train, axis=0)
        L_in = np.concatenate(L_train, axis=0)
        P_in = np.concatenate(P_train, axis=0)


    else:
        print "Asymmetric Style"
        I=np.arange(len(X_in_default))
        rng.shuffle(I)
        X_in = X_in_default[I]
        Y_in = Y_in_default[I]
        L_in = L_in_default[I]
        P_in = P_in_default[I]

    print("After batch processing...")
    print(X_in.shape, Y_in.shape, L_in.shape, P_in.shape)

    """ Start Training """

    start=time.time()
    E = theano.shared(np.concatenate([X_in, P_in[...,np.newaxis]], axis=-1), borrow=True)
    F = theano.shared(Y_in, borrow=True)
    trainer.train(network, E, F, filename='./Parameters/' + mname + '/Fewshot/fewshot_network.npz', restart=False, shuffle=False)
    end=time.time()
    elapsed = np.array([end-start])
    print "\n Time to train style: " + style +": " + str(end-start)
    time_dict[style] = elapsed   # These dictionaries can be pickled if this data is required
    loss_dict[style] = trainer.train_losses

    """ Save Network """

    save_network(network)

outfile = open('./Training_Stats/' + mname + '_fewshot_time','wb')
pickle.dump(time_dict, outfile)
outfile.close()

