# Lecture 3: Gradient descent and learning rates

## Why we need an optimiser

Training a model means choosing parameters $\theta$ that minimise a loss $L(\theta)$ over the training data. For linear regression with squared error there is a closed-form solution, but for almost every other model, including logistic regression and neural networks, there is not. Gradient descent is the general-purpose answer: start somewhere, look at the slope of the loss, and take a small step downhill. Repeat until the loss stops falling.

The gradient $\nabla L(\theta)$ is the vector of partial derivatives of the loss with respect to each parameter. It points in the direction of steepest increase, so the update moves the other way:

$$\theta_{t+1} = \theta_t - \eta \, \nabla L(\theta_t)$$

where $\eta$ is the learning rate, a positive scalar that sets the step size.

## Choosing the learning rate

The learning rate is the single most important hyperparameter of gradient descent. If $\eta$ is too small, training converges but takes a very large number of steps. If $\eta$ is too large, each step overshoots the minimum; the loss oscillates or diverges to infinity. For a convex quadratic loss with curvature $\lambda_{max}$ (the largest eigenvalue of the Hessian), plain gradient descent is stable only when $\eta < 2 / \lambda_{max}$.

In practice the learning rate is found by trying values on a log scale (for example 0.1, 0.03, 0.01, 0.003) and watching the training loss for the first few hundred steps. Learning rate schedules then reduce $\eta$ during training: step decay halves it every few epochs, cosine annealing lowers it smoothly to near zero, and warm-up starts small for the first steps to avoid early instability.

## Batch, stochastic and mini-batch gradient descent

Batch gradient descent computes the gradient over the whole training set before every update. It is exact but expensive, and impossible when the data does not fit in memory.

Stochastic gradient descent (SGD) uses the gradient of a single randomly chosen example. Each step is cheap and noisy; the noise helps escape shallow local minima and saddle points, but the loss curve is jagged.

Mini-batch gradient descent is the compromise used almost everywhere: the gradient is averaged over a batch of typically 32 to 512 examples. It uses vectorised hardware efficiently and its noise level falls as the batch size grows.

## Momentum and adaptive methods

Momentum keeps a running average of past gradients, $v_{t+1} = \beta v_t + \nabla L(\theta_t)$, and steps along $v$ instead of the raw gradient. With $\beta \approx 0.9$ it speeds up progress along consistent directions and damps oscillation across narrow valleys.

Adam combines momentum with a per-parameter step size scaled by a running average of squared gradients, so parameters with large, noisy gradients take smaller steps. It is the default optimiser for most deep learning work because it is forgiving of the initial learning rate, although well-tuned SGD with momentum often generalises slightly better on image classification.

## Common exam points

- State the update rule and explain the sign: the gradient points uphill.
- Explain what happens when the learning rate is too large or too small, ideally with a sketch of the loss curve.
- Compare batch, stochastic and mini-batch gradient descent in terms of cost per step, noise and memory.
- Explain how momentum reduces oscillation.
