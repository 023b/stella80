# Lecture 4: Backpropagation

## From gradient descent to neural networks

Lecture 3 showed that gradient descent only needs one thing from a model: the gradient of the loss with respect to every parameter. A neural network can have millions of parameters, so computing each partial derivative separately would be far too slow. Backpropagation computes all of them in one backward pass, at roughly the cost of one forward pass.

A feed-forward network is a chain of layers. Layer $l$ computes a pre-activation $z^{(l)} = W^{(l)} a^{(l-1)} + b^{(l)}$ and an activation $a^{(l)} = \sigma(z^{(l)})$, where $\sigma$ is a non-linear activation function such as ReLU or the sigmoid. The last layer feeds the loss function $L$, for example cross-entropy for classification.

## The chain rule, layer by layer

Backpropagation is the chain rule applied in reverse order. Define the error signal of layer $l$ as $\delta^{(l)} = \partial L / \partial z^{(l)}$. Then

$$\delta^{(l)} = \left(W^{(l+1)}\right)^\top \delta^{(l+1)} \odot \sigma'(z^{(l)})$$

and the gradients of the parameters follow directly:

$$\frac{\partial L}{\partial W^{(l)}} = \delta^{(l)} \left(a^{(l-1)}\right)^\top, \qquad \frac{\partial L}{\partial b^{(l)}} = \delta^{(l)}$$

The forward pass stores every $z^{(l)}$ and $a^{(l)}$; the backward pass reuses them. This is why training needs much more memory than inference.

## Vanishing and exploding gradients

Each backward step multiplies by a weight matrix and by $\sigma'$. The sigmoid's derivative is at most 0.25, so in a deep sigmoid network the error signal shrinks layer after layer and the early layers barely learn: the vanishing gradient problem. If the weights are large the product grows instead and the gradients explode, so gradient descent takes huge, unstable steps, the same failure as a learning rate that is too large.

Three standard remedies: ReLU activations, whose derivative is exactly 1 for positive inputs; careful weight initialisation (Xavier for tanh, He for ReLU) that keeps the variance of activations constant across layers; and residual connections, which give the gradient a direct path around each block. Gradient clipping caps the gradient norm and is the usual fix for exploding gradients in recurrent networks.

## Training loop

One step of mini-batch gradient descent on a network:

1. Forward pass on a mini-batch, computing the loss.
2. Backward pass, computing $\delta^{(l)}$ and the parameter gradients for every layer.
3. Update every parameter with the optimiser (SGD with momentum or Adam), using the learning rate from the schedule.

Frameworks such as PyTorch implement step 2 automatically with automatic differentiation: they record the operations of the forward pass as a computational graph and walk it backwards.

## Common exam points

- Derive $\delta^{(l)}$ for a two-layer network with a sigmoid hidden layer and squared error.
- Explain why the forward activations must be stored.
- Explain the vanishing gradient problem and two ways to reduce it.
