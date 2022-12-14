"""
Various types of TSP utilizing local planners for distance estimation and path planning
@author: P. Petracek & V. Kratky & P.Vana & P.Cizek & R.Penicka
"""

import numpy as np

from random import randint

from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances
from scipy.spatial.kdtree import KDTree

from utils import *
from path_planners.grid_based.grid_3d import Grid3D
from path_planners.grid_based.astar   import AStar
from path_planners.sampling_based.rrt import RRT, check_line_of_sight

from solvers.LKHInvoker import LKHInvoker

class TSPSolver3D():

    ALLOWED_PATH_PLANNERS               = ('euclidean', 'astar', 'rrt', 'rrtstar')
    ALLOWED_DISTANCE_ESTIMATION_METHODS = ('euclidean', 'astar', 'rrt', 'rrtstar')
    GRID_PLANNERS                       = ('astar', )

    def __init__(self):
        self.lkh = LKHInvoker()

    # # #{ setup()
    def setup(self, problem, path_planner, viewpoints):
        """setup objects required in path planning methods"""

        if path_planner is None:
            return

        assert path_planner['path_planning_method'] in self.ALLOWED_PATH_PLANNERS, 'Given method to compute path (%s) is not allowed. Allowed methods: %s' % (path_planner, self.ALLOWED_PATH_PLANNERS)
        assert path_planner['distance_estimation_method'] in self.ALLOWED_DISTANCE_ESTIMATION_METHODS, 'Given method for distance estimation (%s) is not allowed. Allowed methods: %s' % (path_planner, self.ALLOWED_DISTANCE_ESTIMATION_METHODS)

        # Setup environment
        if path_planner['path_planning_method'] != 'euclidean' or path_planner['distance_estimation_method'] != 'euclidean':

            # setup KD tree for collision queries
            obstacles_array = np.array([[opt.x, opt.y, opt.z] for opt in problem.obstacle_points])
            path_planner['obstacles_kdtree'] = KDTree(obstacles_array)

            # setup environment bounds
            xs = [p.x for p in problem.safety_area]
            ys = [p.y for p in problem.safety_area]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            path_planner['bounds'] = Bounds(Point(x_min, y_min, problem.min_height), Point(x_max, y_max, problem.max_height))

        # Setup 3D grid for grid-based planners
        if path_planner['path_planning_method'] in self.GRID_PLANNERS or path_planner['distance_estimation_method'] in self.GRID_PLANNERS:

            # construct grid
            x_list = [opt.x for opt in problem.obstacle_points]
            x_list.extend([tar.pose.point.x for tar in viewpoints])
            y_list = [opt.y for opt in problem.obstacle_points]
            y_list.extend([tar.pose.point.y for tar in viewpoints])
            z_list = [opt.z for opt in problem.obstacle_points]
            z_list.extend([tar.pose.point.z for tar in viewpoints])
            min_x = np.min(x_list)
            max_x = np.max(x_list)
            min_y = np.min(y_list)
            max_y = np.max(y_list)
            min_z = np.min(z_list)
            max_z = np.max(z_list)

            dim_x = int(np.ceil((max_x - min_x) / path_planner['astar/grid_resolution']))+1
            dim_y = int(np.ceil((max_y - min_y) / path_planner['astar/grid_resolution']))+1
            dim_z = int(np.ceil((max_z - min_z) / path_planner['astar/grid_resolution']))+1

            path_planner['grid'] = Grid3D(idx_zero = (min_x, min_y,min_z), dimensions=(dim_x,dim_y,dim_z), resolution_xyz=path_planner['astar/grid_resolution'])
            path_planner['grid'].setObstacles(problem.obstacle_points, path_planner['safety_distance'])

    # # #}

    # #{ plan_tour()

    def plan_tour(self, problem, viewpoints, path_planner=None):
        '''
        Solve TSP on viewpoints with given goals and starts

        Parameters:
            problem (InspectionProblem): task problem
            viewpoints (list[Viewpoint]): list of Viewpoint objects
            path_planner (dict): dictionary of parameters

        Returns:
            path (list): sequence of points with start equaling the end
        '''

        # Setup 3D grid for grid-based planners and KDtree for sampling-based planners
        self.setup(problem, path_planner, viewpoints)

        self.tooFarAwayPairs = []   # Tracking distance heuristic
        self.tooFarAwayDistances = {}

        n              = len(viewpoints)
        self.distances = np.full((n, n), -1.0)  # Fill with a negative sentinel value
        np.fill_diagonal(self.distances, 0)     # Make sure that everything is 0, as it should be
        self.paths = {}

        # print("Viewpoints:", [p.pose.asList() for p in viewpoints])

        # find path between each pair of goals (a, b)
        for a in range(n):
            for b in range(n):
                # Pairwise distances have been calculated already
                if a == b or self.distances[a][b]!=-1.0:
                    continue

                #   - Play with distance estimates in TSP (tsp/distance_estimates parameter in config) and see how it influences the solution
                #   - You will probably see that computing for all poses from both sets takes a long time.
                #   - Think if you can limit the number of computations or decide which distance-estimating method use for each point-pair.
                #       --> The distance matrix is symmetric. Avoid calculating identical values.
                #       --> Note that the straighten_paths param that has been set means that
                #           we will end up with straight paths from any two points that have
                #           line-of-sight. Therefore, we can use the euclidean distance 
                #           estimate without loss of performance in this case.
                #           In other cases, we can use RRT(*) for a better estimate.

                # get poses of the viewpoints
                g1 = viewpoints[a].pose
                g2 = viewpoints[b].pose

                # print(f"Checking between idx:{a} {g1} and idx:{b} {g2}")

                # estimate distances between the viewpoints
                # If there is a straight line connecting g1 and g2, just use euclidean.
                # Else, we try to use rrtstar.

                # If I had more time I would refactor RRT.validateLinePath to be a class 
                # method, but probably it's alright just to rip out the logic to use here.

                # First, check the distances
                path, distance = self.compute_path(g1, g2, path_planner, path_planner_method='euclidean')
                los = check_line_of_sight(g1.asList(), g2.asList(), path_planner, check_bounds=False)

                DIST_THRESHOLD = 5.0    # tunable
                if not los:
                    if distance > DIST_THRESHOLD:
                        # Too far away. Do not compute a path.
                        self.tooFarAwayPairs.append( (a,b) )
                        self.tooFarAwayPairs.append( (b,a) )
                        self.tooFarAwayDistances[(a,b)] = distance
                        self.tooFarAwayDistances[(b,a)] = distance
                        
                        # print(a, b, f"is too far away. Distance: {distance:.2f}")
                        distance *= 3 # bias the solver away from straight lines
                    else:
                        # No straight-line path, so we need to use a more accurate estimate.
                        path, distance = self.compute_path(g1, g2, path_planner, 
                            path_planner_method=path_planner['path_planning_method'])

                # store paths/distances in matrices
                path[-1].heading = g2.heading   # Add in the heading of the last member, to ensure symmetry
                # print([p.asList() for p in path])
                self.paths[(a, b)]   = path
                self.paths[(b, a)]   = list(reversed(path))
                self.distances[a][b] = distance
                self.distances[b][a] = distance


        # compute TSP tour
        path = self.compute_tsp_tour(viewpoints, path_planner)

        return path

    # #}

    # # #{ compute_path()

    def compute_path(self, p_from, p_to, path_planner, path_planner_method):
        '''
        Computes collision-free path (if feasible) between two points

        Parameters:
            p_from (Pose): start
            p_to (Pose): to
            path_planner (dict): dictionary of parameters
            path_planner_method (string): method of path planning

        Returns:
            path (list[Pose]): sequence of points
            distance (float): length of path
        '''
        path, distance = [], float('inf')

        # Use Euclidean metric
        if path_planner is None or path_planner_method == 'euclidean':

            path, distance = [p_from, p_to], distEuclidean(p_from, p_to)

        # Plan with A*
        elif path_planner_method == 'astar':

            astar = AStar(path_planner['grid'], path_planner['safety_distance'], path_planner['timeout'], path_planner['straighten'])
            path, distance = astar.generatePath(p_from.asList(), p_to.asList())
            if path:
                path = [Pose(p[0], p[1], p[2], p[3]) for p in path]

        # Plan with RRT/RRT*
        elif path_planner_method.startswith('rrt'):

            rrt = RRT()
            path, distance = rrt.generatePath(p_from.asList(), p_to.asList(), path_planner, rrtstar=(path_planner_method == 'rrtstar'), straighten=path_planner['straighten'])
            if path:
                path = [Pose(p[0], p[1], p[2], p[3]) for p in path]

        if path is None or len(path) == 0:
            rospy.logerr('No path found. Shutting down.')
            rospy.signal_shutdown('No path found. Shutting down.');
            exit(-2)

        return path, distance

    # # #}

    # #{ compute_tsp_tour()

    def compute_tsp_tour(self, viewpoints, path_planner):
        '''
        Compute the shortest tour based on the distance matrix (self.distances) and connect the path throught waypoints

        Parameters:
            viewpoints (list[Viewpoint]): list of VPs
            path_planner (dict): dictionary of parameters

        Returns:
            path (list[Poses]): sequence of points with start equaling the end
        '''

        # compute the shortest sequence given the distance matrix
        sequence = self.compute_tsp_sequence()
        
        # print("TSP sequence:", sequence)
        # for a, b in self.tooFarAwayPairs:
        #     print(f"{a}, {b}, {self.tooFarAwayDistances[(a,b)]:.2f}")

        for i in range(1, len(sequence)):
            a, b = sequence[i-1], sequence[i]
            if (a,b) in self.tooFarAwayPairs:
                print(f"Path between {a} and {b} was not calculated. Dist matrix: {self.distances[a][b]} Original dist: {self.tooFarAwayDistances[(a,b)]:.2f}")
                print("Calculating solution now")

                g1 = viewpoints[a].pose
                g2 = viewpoints[b].pose

                self.paths[(a,b)], _ = self.compute_path(g1, g2, path_planner, 
                            path_planner_method=path_planner['path_planning_method'])
                

        path = []
        n    = len(self.distances)

        for a in range(n):
            b = (a + 1) % n
            a_idx       = sequence[a]
            b_idx       = sequence[b]

            '''
            # if the paths are already computed
            if path_planner['distance_estimation_method'] == path_planner['path_planning_method']:
                actual_path = self.paths[(a_idx, b_idx)]
            # if the path planning and distance estimation methods differ, we need to compute the path
            else:
                actual_path, _ = self.compute_path(viewpoints[a_idx].pose, viewpoints[b_idx].pose, path_planner, path_planner['path_planning_method'])
            '''

            # Based on the logic in `plan_tour`, we should not need to replan paths
            actual_path = self.paths[(a_idx, b_idx)]

            # join paths
            path = path + actual_path[:-1]

            # force flight to end point
            if a == (n - 1):
                path = path + [viewpoints[b_idx].pose]

        return path

    # #}

    # # #{ compute_tsp_sequence()

    def compute_tsp_sequence(self):
        '''
        Compute the shortest sequence based on the distance matrix (self.distances) using LKH

        Returns:
            sequence (list): sequence of viewpoints ordered optimally w.r.t the distance matrix
        '''

        n = len(self.distances)

        # print("Distances before computing TSP sequence:")
        # for i in range(n):
        #     print([int(elem*10) for elem in self.distances[i,:]])


        fname_tsp = "problem"
        user_comment = "a comment by the user"
        self.lkh.writeTSPLIBfile_FE(fname_tsp, self.distances, user_comment)
        self.lkh.run_LKHsolver_cmd(fname_tsp, silent=True)
        sequence = self.lkh.read_LKHresult_cmd(fname_tsp)

        if len(sequence) > 0 and sequence[0] is not None:
            for i in range(len(sequence)):
                if sequence[i] is None:
                    new_sequence = sequence[i:len(sequence)] + sequence[:i]
                    sequence = new_sequence
                    break

        return sequence

    # # #}

    # #{ clusterViewpoints()

    def clusterViewpoints(self, problem, viewpoints, method):
        '''
        Clusters viewpoints into K (number of robots) clusters.

        Parameters:
            problem (InspectionProblem): task problem
            viewpoints (list): list of Viewpoint objects
            method (string): method ('random', 'kmeans')

        Returns:
            clusters (Kx list): clusters of points indexed for each robot:
        '''
        k = problem.number_of_robots

        ## | ------------------- K-Means clustering ------------------- |
        if method == 'kmeans':
            # Prepare positions of the viewpoints in the world
            positions = np.array([vp.pose.point.asList() for vp in viewpoints])

            #  - utilize sklearn.cluster.KMeans implementation (https://scikit-learn.org/stable/modules/generated/sklearn.cluster.KMeans.html)
            kmeans_res = KMeans(n_clusters=k).fit(positions)

            #  - after finding the labels, you may want to swap the classes (e.g., by looking at the distance of the UAVs from the cluster centers)
            # Get pairwise distances between cluster centers and initial UAV positions
            cluster_ctrs = kmeans_res.cluster_centers_
            start_poses = np.array([
                [
                    problem.start_poses[i].position.x,
                    problem.start_poses[i].position.y,
                    problem.start_poses[i].position.z,
                ] for i in range(k)
            ])

            # We use euclidean distance as the pairwise distance metric
            d_mat = pairwise_distances(cluster_ctrs, start_poses)

            # Mapping cluster index to UAV index
            assigned_clusters = dict.fromkeys(range(k), -1)
            
            # Assign index to the cluster center with the minimum distance.
            # Implicitly, UAV1 has priority over UAV2.
            for ctr_it in range(k):
                min_dist = np.inf
                for uav_it in range(k):
                    # Check for minimal distance
                    if d_mat[ctr_it, uav_it] < min_dist:
                        # Check that there are no duplicates
                        if uav_it not in assigned_clusters.values():
                            min_dist = d_mat[ctr_it, uav_it]
                            assigned_clusters[ctr_it] = uav_it

            # for debugging
            # print("Distance Matrix")
            # print(d_mat)
            # print("Assigned clusters")
            # print(assigned_clusters)

            assert len(assigned_clusters) == len(set(assigned_clusters.values())), f"There is a duplicate assignment during clustering. assigned_clusters: f{assigned_clusters}"

            labels = kmeans_res.labels_
            for i in range(len(labels)):
                # Simply call the mapping from cluster index to UAV index
                labels[i] = assigned_clusters[labels[i]]


        ## | -------------------- Random clustering ------------------- |
        else:
            labels = [randint(0, k - 1) for vp in viewpoints]

        # Store as clusters (2D array of viewpoints)
        clusters = []
        for r in range(k):
            clusters.append([])

            for label in range(len(labels)):
                if labels[label] == r:
                    clusters[r].append(viewpoints[label])

        return clusters

    # #}
