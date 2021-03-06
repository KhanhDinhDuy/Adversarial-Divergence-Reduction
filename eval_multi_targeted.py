"""
Infinite evaluation loop going through the checkpoints in the model directory
as they appear and evaluating them. Accuracy and average loss are printed and
added as tensorboard summaries.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import json
import math
import os
import sys
import time
import numpy as np 
import tensorflow as tf

import cifar10_input
from pgd_attack import LinfPGDAttack

from mysetting import * 
from pie.utils.utils_cm import writelog 
from pie.utils.utils_cm import mkdir_p 

from model import Model


atk_conf = dict()
atk_conf['num_eval_examples'] = 10000
atk_conf['eval_batch_size'] = 100 
atk_conf['eval_on_cpu'] = False
atk_conf['data_path'] = "cifar10_data"
atk_conf['epsilon'] = 8.0 
atk_conf['num_steps'] = 20
atk_conf['step_size'] = 2.0 
atk_conf['random_start'] = True 
atk_conf['loss_func'] = 'xent'

# Global constants
num_eval_examples = atk_conf['num_eval_examples']
eval_batch_size = atk_conf['eval_batch_size']
eval_on_cpu = atk_conf['eval_on_cpu']
data_path = atk_conf['data_path']

model_name = '_'.join([t.format(v) for (t, v) in setup])
model_dir = os.path.join('models', model_name)
log_dir = os.path.join('logs', model_name)
log_file = log_dir + '/logfile.txt'

writelog('-------------------', log_file)
writelog('MULTITARGETED ATTACK', log_file)
for key in atk_conf.keys(): 
  writelog('{}:{}'.format(key, atk_conf[key]), log_file)
writelog('-------------------', log_file)

# Set upd the data, hyperparameters, and the model
cifar = cifar10_input.CIFAR10Data(data_path)

if eval_on_cpu:
  with tf.device("/cpu:0"):
    model = Model(mode='eval', mtype=args.model)
    attack = LinfPGDAttack(model,
                           atk_conf['epsilon'],
                           atk_conf['num_steps'],
                           atk_conf['step_size'],
                           atk_conf['random_start'],
                           atk_conf['loss_func'], 
                           targeted=True)
else:
  model = Model(mode='eval', mtype=args.model)
  attack = LinfPGDAttack(model,
                         atk_conf['epsilon'],
                         atk_conf['num_steps'],
                         atk_conf['step_size'],
                         atk_conf['random_start'],
                         atk_conf['loss_func'], 
                         targeted=True)

global_step = tf.contrib.framework.get_or_create_global_step()

# Setting up the Tensorboard and checkpoint outputs
if not os.path.exists(model_dir):
  os.makedirs(model_dir)
eval_dir = os.path.join(model_dir, 'eval')
if not os.path.exists(eval_dir):
  os.makedirs(eval_dir)

last_checkpoint_filename = ''
already_seen_state = False

saver = tf.train.Saver()
summary_writer = tf.summary.FileWriter(eval_dir)

# A function for evaluating a single checkpoint
def evaluate_checkpoint(filename):
  with tf.Session() as sess:
    # Restore the checkpoint
    saver.restore(sess, filename)

    # Iterate over the samples batch-by-batch
    num_batches = int(math.ceil(num_eval_examples / eval_batch_size))
    total_xent_nat = 0.
    total_xent_adv = 0.
    total_corr_nat = 0
    total_corr_adv = 0

    for ibatch in range(num_batches):
      bstart = ibatch * eval_batch_size
      bend = min(bstart + eval_batch_size, num_eval_examples)

      x_batch = cifar.eval_data.xs[bstart:bend, :]
      y_batch = cifar.eval_data.ys[bstart:bend]

      dict_nat = {model.x_input: x_batch,
                  model.y_input: y_batch}
      print(eval_batch_size, ibatch, num_batches)

      cur_corr_nat, cur_xent_nat = sess.run(
                                  [model.num_correct,model.xent],
                                  feed_dict = dict_nat)       

      total_xent_nat += cur_xent_nat
      total_corr_nat += cur_corr_nat

      batch_xent_adv = 0
      batch_corr_adv = []

      for ii in range(10):

        y_adv = ii * np.ones_like(y_batch)
        
        x_batch_adv = attack.perturb(x_batch, y_adv, sess)

        dict_adv = {model.x_input: x_batch_adv,
                    model.y_input: y_batch}

        cur_logits, cur_xent_adv = sess.run(
                                        [model.pre_softmax,model.xent],
                                        feed_dict = dict_adv)
        cur_pred = np.argmax(cur_logits, 1) # [b,]
        assert(np.shape(y_batch) == np.shape(cur_pred))
        cur_pred = (cur_pred == y_batch)
        cur_pred = np.expand_dims(cur_pred, axis=1)
        batch_corr_adv.append(cur_pred)
        
        batch_xent_adv += cur_xent_adv

      batch_corr_adv = np.concatenate(batch_corr_adv, axis=1)
      batch_corr_adv = np.all(batch_corr_adv, 1)
      batch_corr_adv = sum(batch_corr_adv)

      print("Correctly classified natural examples: {}".format(cur_corr_nat))
      print("Correctly classified adversarial examples: {}".format(batch_corr_adv))

      total_xent_adv += batch_xent_adv / 10. 
      total_corr_adv += batch_corr_adv

    avg_xent_nat = total_xent_nat / num_eval_examples
    avg_xent_adv = total_xent_adv / num_eval_examples
    acc_nat = total_corr_nat / num_eval_examples
    acc_adv = total_corr_adv / num_eval_examples

    summary = tf.Summary(value=[
          tf.Summary.Value(tag='xent adv eval', simple_value= avg_xent_adv),
          tf.Summary.Value(tag='xent adv', simple_value= avg_xent_adv),
          tf.Summary.Value(tag='xent nat', simple_value= avg_xent_nat),
          tf.Summary.Value(tag='accuracy adv eval', simple_value= acc_adv),
          tf.Summary.Value(tag='accuracy adv', simple_value= acc_adv),
          tf.Summary.Value(tag='accuracy nat', simple_value= acc_nat)])
    summary_writer.add_summary(summary, global_step.eval(sess))

    writelog('natural: {:.2f}%'.format(100 * acc_nat), log_file)
    writelog('adversarial: {:.2f}%'.format(100 * acc_adv), log_file)
    writelog('avg nat loss: {:.4f}'.format(avg_xent_nat), log_file)
    writelog('avg adv loss: {:.4f}'.format(avg_xent_adv), log_file)

# Infinite eval loop
cur_checkpoint = tf.train.latest_checkpoint(model_dir)
evaluate_checkpoint(cur_checkpoint)

