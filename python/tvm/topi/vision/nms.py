# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# pylint: disable=import-error, invalid-name, no-member, too-many-locals, too-many-arguments, undefined-variable, too-many-nested-blocks, too-many-branches, too-many-statements, too-many-function-args
"""Non-maximum suppression operator"""
import tvm
from tvm import te

from tvm.te import hybrid
from ..sort import argsort


@hybrid.script
def hybrid_rearrange_box_out(data, one, batch_size, num_anchors):
    """Hybrid routine to rearrange nms output to
    move all valid entries to top.

    Parameters
    ----------
    data : tvm.te.Tensor or numpy NDArray
        NMS output. 3-D tensor with shape
        [batch_size, num_anchors, 6].

    one: tvm.tir.const
        Constant one with the same dtype as data.

    batch_size: tvm.tir.IntImm or tvm.tir.Var
        Batch size. We need to pass it in since hybrid script doesn't support
        binding variable to symbolic dim.

    num_anchors: tvm.tir.IntImm or tvm.tir.Var
        Number of anchors.

    Returns
    -------
    output : tvm.te.Tensor or numpy NDArray
        Transformed NMS output. 3-D tensor with shape
        [batch_size, num_anchors, 6].
    """
    elem_length = data.shape[2]
    output = output_tensor((batch_size, num_anchors, elem_length), data.dtype)

    for i in parallel(batch_size):
        valid_idx = 0
        for j in range(num_anchors):
            if data[i, j, 0] >= 0:
                for k in range(elem_length):
                    output[i, valid_idx, k] = data[i, j, k]
                valid_idx += 1
            if j >= valid_idx:
                for k in range(elem_length):
                    output[i, j, k] = -one
    return output


@hybrid.script
def hybrid_rearrange_indices_out(data, one, batch_size, num_anchors):
    """Hybrid routine to rearrange nms output to
    move all valid entries to top.

    Parameters
    ----------
    data : tvm.te.Tensor or numpy NDArray
        NMS output. 3-D tensor with shape
        [batch_size, num_anchors, 6] or
        [batch_size, num_anchors, 5], or 2-D
        tensor with shape [batch_size, num_anchors].

    one: tvm.tir.const
        Constant one with the same dtype as data.

    batch_size: tvm.tir.IntImm or tvm.tir.Var
        Batch size. We need to pass it in since hybrid script doesn't support
        binding variable to symbolic dim.

    num_anchors: tvm.tir.IntImm or tvm.tir.Var
        Number of anchors.

    Returns
    -------
    output : tvm.te.Tensor or numpy NDArray
        2-D tensor with shape [batch_size, num_anchors].

    valid_box_count : tvm.te.Tensor or numpy NDArray
        Tensor with shape [batch_size, 1], indicates
        the valid number of boxes.
    """
    valid_box_count = output_tensor((batch_size, 1), "int32")
    output = output_tensor((batch_size, num_anchors), data.dtype)

    for i in parallel(batch_size):
        valid_idx = 0
        for j in range(num_anchors):
            if data[i, j] >= 0:
                output[i, valid_idx] = data[i, j]
                valid_idx += 1
            if data[i, j] > num_anchors or data[i, j] < -num_anchors:
                output[i, valid_idx] = 0
                valid_idx += 1
            if j >= valid_idx:
                output[i, j] = -one
        valid_box_count[i, 0] = valid_idx

    return output, valid_box_count


@hybrid.script
def hybrid_get_valid_counts(
    data, score_threshold, id_index, score_index, one, batch_size, num_anchors
):
    """Hybrid routine to get valid count of bounding boxes
    given a score threshold. Also moves valid boxes to the
    top of input data.

    Parameters
    ----------
    data : tvm.te.Tensor or numpy NDArray
        Input data. 3-D tensor with shape [batch_size, num_anchors, 6]
        or [batch_size, num_anchors, 5].

    score_threshold : tvm.tir.const
        Lower limit of score for valid bounding boxes.

    id_index : tvm.tir.const
        index of the class categories, -1 to disable.

    score_index: tvm.tir.const
        Index of the scores/confidence of boxes.

    one: tvm.tir.const
        Constant one with the same dtype as data.

    batch_size: tvm.tir.IntImm or tvm.tir.Var
        Batch size. We need to pass it in since hybrid script doesn't support
        binding variable to symbolic dim.

    num_anchors: tvm.tir.IntImm or tvm.tir.Var
        Number of anchors.

    Returns
    -------
    valid_count : tvm.te.Tensor or numpy NDArray
        1-D tensor for valid number of boxes.

    out_tensor : tvm.te.Tensor or numpy NDArray
        Rearranged data tensor.

    out_indices: tvm.te.Tensor or numpy NDArray
        Related index in input data.
    """
    box_data_length = data.shape[2]
    valid_count = output_tensor((batch_size,), "int32")
    out_tensor = output_tensor((batch_size, num_anchors, box_data_length), data.dtype)
    out_indices = output_tensor((batch_size, num_anchors), "int32")
    for i in parallel(batch_size):
        valid_count[i] = 0
        for j in range(num_anchors):
            score = data[i, j, score_index]
            if score > score_threshold and (id_index < 0 or data[i, j, id_index] >= 0):
                for k in range(box_data_length):
                    out_tensor[i, valid_count[i], k] = data[i, j, k]
                out_indices[i, valid_count[i]] = j
                valid_count[i] += 1
            if j >= valid_count[i]:
                for k in range(box_data_length):
                    out_tensor[i, j, k] = -one
                out_indices[i, j] = -1
    return valid_count, out_tensor, out_indices


def get_valid_counts(data, score_threshold=0, id_index=0, score_index=1):
    """Get valid count of bounding boxes given a score threshold.
    Also moves valid boxes to the top of input data.

    Parameters
    ----------
    data : tvm.te.Tensor
        Input data. 3-D tensor with shape [batch_size, num_anchors, 6]
        or [batch_size, num_anchors, 5].

    score_threshold : optional, float
        Lower limit of score for valid bounding boxes.

    id_index : optional, int
        index of the class categories, -1 to disable.

    score_index: optional, int
        Index of the scores/confidence of boxes.

    Returns
    -------
    valid_count : tvm.te.Tensor
        1-D tensor for valid number of boxes.

    out_tensor : tvm.te.Tensor
        Rearranged data tensor.

    out_indices: tvm.te.Tensor or numpy NDArray
        Related index in input data.
    """
    score_threshold_const = tvm.tir.const(score_threshold, data.dtype)
    id_index_const = tvm.tir.const(id_index, "int32")
    score_index_const = tvm.tir.const(score_index, "int32")
    return hybrid_get_valid_counts(
        data,
        score_threshold_const,
        id_index_const,
        score_index_const,
        tvm.tir.const(1, data.dtype),
        data.shape[0],
        data.shape[1],
    )


@hybrid.script
def hybrid_nms(
    data,
    sorted_index,
    valid_count,
    indices,
    batch_size,
    num_anchors,
    max_output_size,
    iou_threshold,
    force_suppress,
    top_k,
    coord_start,
    score_index,
    id_index,
    return_indices,
    zero,
    one,
):
    """Hybrid routing for non-maximum suppression.

    Parameters
    ----------
    data: tvm.te.Tensor or numpy NDArray
        Bounding boxes with class and score. 3-D tensor with shape
        [batch_size, num_anchors, 6]. It could be the second output
        out_tensor of get_valid_counts.

    sorted_index : tvm.te.Tensor or numpy NDArray
        Bounding box indexes sorted by score, with shape
        [batch_size, num_anchors].

    valid_count : tvm.te.Tensor or numpy NDArray
        1-D tensor for valid number of boxes. It could be the output
        valid_count of get_valid_counts.

    indices : tvm.te.Tensor or numpy.NDArray
        indices in original tensor, with shape [batch_size, num_anchors],
        represents the index of box in original data. It could be the third
        output out_indices of get_valid_counts. The values in the second
        dimension are like the output of arange(num_anchors) if get_valid_counts
        is not used before non_max_suppression.

    batch_size: tvm.tir.IntImm or tvm.tir.Var
        Batch size. We need to pass it in since hybrid script doesn't support
        binding variable to symbolic dim.

    num_anchors: tvm.tir.IntImm or tvm.tir.Var
        The number of anchors.

    max_output_size : tvm.te.Tensor
        Max number of output valid boxes for each instance.
        Return all valid boxes if max_output_size < 0.

    iou_threshold : tvm.tir.const
        Overlapping(IoU) threshold to suppress object with smaller score.

    force_suppress : tvm.tir.const
        Whether to suppress all detections regardless of class_id.

    top_k : tvm.tir.const
        Keep maximum top k detections before nms, -1 for no limit.

    coord_start : tvm.tir.const
        Start index of the consecutive 4 coordinates.

    score_index: tvm.tir.const
        Index of the scores/confidence of boxes.

    id_index : tvm.tir.const
        index of the class categories, -1 to disable.

    return_indices : tvm.tir.const
        Whether to return box indices in input data.

    zero: tvm.tir.const
        Constant zero with the same dtype as data.

    one: tvm.tir.const
        Constant one with the same dtype as data.

    Returns
    -------
    output : tvm.te.Tensor
        3-D tensor with shape [batch_size, num_anchors, 6]
        or [batch_size, num_anchors, 5].

    box_indices: tvm.te.Tensor
        2-D tensor with shape [batch_size, num_anchors].
    """

    box_data_length = data.shape[2]

    # box_indices is the expected indices of boxes
    box_indices = output_tensor((batch_size, num_anchors), sorted_index.dtype)
    output = output_tensor(
        (
            batch_size,
            num_anchors,
            box_data_length,
        ),
        data.dtype,
    )

    for i in range(batch_size):
        if iou_threshold > 0:
            if valid_count[i] > 0:
                # Reorder output
                nkeep = valid_count[i]
                if 0 < top_k < nkeep:
                    nkeep = top_k
                for j in parallel(nkeep):
                    for k in range(box_data_length):
                        output[i, j, k] = data[i, sorted_index[i, j], k]
                    box_indices[i, j] = sorted_index[i, j]
                if 0 < top_k < valid_count[i]:
                    for j in parallel(valid_count[i] - nkeep):
                        for k in range(box_data_length):
                            output[i, j + nkeep, k] = -one
                        box_indices[i, j + nkeep] = -1

            # Apply nms
            box_start_idx = coord_start
            batch_idx = i
            num_valid_boxes = 0

            for j in range(valid_count[i]):
                if num_valid_boxes == max_output_size:
                    for k in range(box_data_length):
                        output[i, j, k] = -one
                    box_indices[i, j] = -1

                elif output[i, j, score_index] > 0:
                    box_a_idx = j
                    is_valid_box = 1

                    # a_l: left, a_t: top, a_r: right, a_b: bottom
                    a_l = min(
                        output[batch_idx, box_a_idx, box_start_idx],
                        output[batch_idx, box_a_idx, box_start_idx + 2],
                    )
                    a_t = min(
                        output[batch_idx, box_a_idx, box_start_idx + 1],
                        output[batch_idx, box_a_idx, box_start_idx + 3],
                    )
                    a_r = max(
                        output[batch_idx, box_a_idx, box_start_idx],
                        output[batch_idx, box_a_idx, box_start_idx + 2],
                    )
                    a_b = max(
                        output[batch_idx, box_a_idx, box_start_idx + 1],
                        output[batch_idx, box_a_idx, box_start_idx + 3],
                    )

                    # check if current box j is valid by calculating iou with
                    # all existing valid boxes
                    for k in range(j):
                        check_iou = 0
                        if (
                            is_valid_box == 1
                            and k < j
                            and output[i, k, score_index] > 0
                            and (id_index < 0 or output[i, k, id_index] >= 0)
                        ):
                            if force_suppress:
                                check_iou = 1
                            elif id_index < 0 or output[i, j, id_index] == output[i, k, id_index]:
                                check_iou = 1

                        if check_iou > 0:
                            box_b_idx = k

                            # b_l: left, b_t: top, b_r: right, b_b: bottom
                            b_l = min(
                                output[batch_idx, box_b_idx, box_start_idx],
                                output[batch_idx, box_b_idx, box_start_idx + 2],
                            )
                            b_t = min(
                                output[batch_idx, box_b_idx, box_start_idx + 1],
                                output[batch_idx, box_b_idx, box_start_idx + 3],
                            )
                            b_r = max(
                                output[batch_idx, box_b_idx, box_start_idx],
                                output[batch_idx, box_b_idx, box_start_idx + 2],
                            )
                            b_b = max(
                                output[batch_idx, box_b_idx, box_start_idx + 1],
                                output[batch_idx, box_b_idx, box_start_idx + 3],
                            )

                            # Overlapping width and height
                            w = max(zero, min(a_r, b_r) - max(a_l, b_l))
                            h = max(zero, min(a_b, b_b) - max(a_t, b_t))

                            # Overlapping area
                            area = h * w

                            # total area of the figure formed by box a and box b
                            # except for overlapping area
                            u = (a_r - a_l) * (a_b - a_t) + (b_r - b_l) * (b_b - b_t) - area

                            # get the iou
                            iou = zero if u <= zero else area / u

                            if iou >= iou_threshold:
                                is_valid_box = 0

                    if is_valid_box == 0:
                        for k in range(box_data_length):
                            output[i, j, k] = -one
                        box_indices[i, j] = -1
                    else:
                        num_valid_boxes += 1

        else:
            for j in parallel(valid_count[i]):
                for k in range(box_data_length):
                    output[i, j, k] = data[i, j, k]
                box_indices[i, j] = j

        # Set invalid entry to be -1
        for j in parallel(num_anchors - valid_count[i]):
            for k in range(box_data_length):
                output[i, j + valid_count[i], k] = -one
            box_indices[i, j + valid_count[i]] = -1

        if return_indices:
            for j in range(valid_count[i]):
                idx = box_indices[i, j]
                if box_indices[i, j] >= 0:
                    box_indices[i, j] = indices[i, idx]

    return output, box_indices


@tvm.target.generic_func
def non_max_suppression(
    data,
    valid_count,
    indices,
    max_output_size=-1,
    iou_threshold=0.5,
    force_suppress=False,
    top_k=-1,
    coord_start=2,
    score_index=1,
    id_index=0,
    return_indices=True,
    invalid_to_bottom=False,
):
    """Non-maximum suppression operator for object detection.

    Parameters
    ----------
    data : tvm.te.Tensor
        3-D tensor with shape [batch_size, num_anchors, 6] or [batch_size, num_anchors, 5].

    valid_count : tvm.te.Tensor
        1-D tensor for valid number of boxes.

    indices : tvm.te.Tensor
        2-D tensor with shape [batch_size, num_anchors].

    max_output_size : optional, int or tvm.te.Tensor
        Max number of output valid boxes for each instance.
        Return all valid boxes if the value of max_output_size is less than 0.

    iou_threshold : optional, float
        Non-maximum suppression threshold.

    force_suppress : optional, boolean
        Whether to suppress all detections regardless of class_id.

    top_k : optional, int
        Keep maximum top k detections before nms, -1 for no limit.

    coord_start : required, int
        Start index of the consecutive 4 coordinates.

    score_index: optional, int
        Index of the scores/confidence of boxes.

    id_index : optional, int
        index of the class categories, -1 to disable.

    return_indices : optional, boolean
        Whether to return box indices in input data.

    invalid_to_bottom : optional, boolean
        Whether to move all valid bounding boxes to the top.

    Returns
    -------
    out : tvm.te.Tensor or tuple of tvm.te.Tensor
        3-D tensor with shape [batch_size, num_anchors, 6]
        or [batch_size, num_anchors, 5]. Out is a tuple of tvm.te.Tensor
        if return_indices is True, the Tensor in the tuple is 2-D tensor
        with shape [batch_size, num_anchors] and shape
        [batch_size, num_valid_anchors] respectively.

    Example
    --------
    .. code-block:: python

        # An example to use non_max_suppression
        dshape = (1, 5, 6)
        data = te.placeholder(dshape, name="data")
        valid_count = te.placeholder((dshape[0],), dtype="int32", name="valid_count")
        iou_threshold = 0.7
        force_suppress = True
        top_k = -1
        out = non_max_suppression(data, valid_count, indices, iou_threshold=iou_threshold,
                                  force_suppress=force_suppress, top_k=top_k)
        np_data = np.random.uniform(dshape)
        np_valid_count = np.array([4])
        s = topi.generic.schedule_nms(out)
        f = tvm.build(s, [data, valid_count, out], "llvm")
        ctx = tvm.cpu()
        tvm_data = tvm.nd.array(np_data, ctx)
        tvm_valid_count = tvm.nd.array(np_valid_count, ctx)
        tvm_out = tvm.nd.array(np.zeros(dshape, dtype=data.dtype), ctx)
        f(tvm_data, tvm_valid_count, tvm_out)
    """
    batch_size = data.shape[0]
    num_anchors = data.shape[1]
    if isinstance(max_output_size, int):
        max_output_size = tvm.tir.const(max_output_size, dtype="int32")
    score_axis = score_index
    score_shape = (batch_size, num_anchors)
    score_tensor = te.compute(score_shape, lambda i, j: data[i, j, score_axis])
    sort_tensor = argsort(score_tensor, valid_count=valid_count, axis=1, is_ascend=False)

    out, box_indices = hybrid_nms(
        data,
        sort_tensor,
        valid_count,
        indices,
        batch_size,
        num_anchors,
        max_output_size,
        tvm.tir.const(iou_threshold, dtype=data.dtype),
        tvm.tir.const(force_suppress, dtype="bool"),
        tvm.tir.const(top_k, dtype="int32"),
        tvm.tir.const(coord_start, dtype="int32"),
        tvm.tir.const(score_index, dtype="int32"),
        tvm.tir.const(id_index, dtype="int32"),
        tvm.tir.const(return_indices, dtype="bool"),
        zero=tvm.tir.const(0, dtype=data.dtype),
        one=tvm.tir.const(1, dtype=data.dtype),
    )
    if return_indices:
        return hybrid_rearrange_indices_out(
            box_indices,
            one=tvm.tir.const(1, dtype="int32"),
            batch_size=batch_size,
            num_anchors=num_anchors,
        )

    if invalid_to_bottom:
        out = hybrid_rearrange_box_out(
            out,
            one=tvm.tir.const(1, dtype=data.dtype),
            batch_size=batch_size,
            num_anchors=num_anchors,
        )
    return out
